"""
research-worker / extract_mineru.py
Extração estruturada de PDFs com MinerU (local, CPU):
  - equações em LaTeX
  - cabeçalhos/rodapés/numeração de página removidos
  - blocos tipados (título, seção, texto, equação, figura, referência)
E chunking por seção, sem partir equações no meio.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import fitz  # pymupdf
import tiktoken

log = logging.getLogger(__name__)

MINERU_TIER    = os.getenv("MINERU_TIER", "standard")
MINERU_TIMEOUT = int(os.getenv("MINERU_TIMEOUT", "7200"))   # segundos por PDF
# "detect" → OCR só quando a camada de texto do PDF tem fontes de símbolos quebradas
# "auto" | "txt" | "ocr" → repassado direto ao MinerU
MINERU_OCR_MODE = os.getenv("MINERU_OCR_MODE", "detect")
CHUNK_SIZE     = int(os.getenv("CHUNK_SIZE", "512"))        # tokens
CHUNK_OVERLAP  = int(os.getenv("CHUNK_OVERLAP", "64"))      # tokens

tokenizer = tiktoken.get_encoding("cl100k_base")

# Blocos de layout que não são conteúdo
SKIP_TYPES   = {"header", "footer", "page_number", "page_footnote"}
REFS_TITLE   = re.compile(r"^\s*(\d+\.?\s*)?(references|bibliography|refer[êe]ncias)", re.I)
DOI_PATTERN  = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+")


# ── Camada de texto quebrada ───────────────────────────────────────────────────

# PDFs antigos (ex.: Elsevier ~2000-2005) mapeiam símbolos matemáticos para
# letras latinas: "=" vira "¼", "+" vira "þ", parênteses viram "ð"/"Þ", e letras
# gregas viram "o", "Z", "t". O texto corrido sai errado mesmo com o MinerU,
# que confia na camada de texto. Nesses casos vale pagar o custo do OCR.
BROKEN_GLYPHS = "¼þðÞ"


def has_broken_text_layer(pdf_path: Path) -> bool:
    with fitz.open(str(pdf_path)) as doc:
        text = "".join(page.get_text("text") for page in doc)
    if not text:
        return False
    broken = sum(text.count(g) for g in BROKEN_GLYPHS)
    return broken / len(text) > 1 / 2000


def choose_ocr_mode(pdf_path: Path) -> str:
    if MINERU_OCR_MODE != "detect":
        return MINERU_OCR_MODE
    if has_broken_text_layer(pdf_path):
        log.info("  Camada de texto com símbolos quebrados: usando OCR completo.")
        return "ocr"
    return "auto"


# ── Execução do MinerU ────────────────────────────────────────────────────────

def run_mineru(pdf_path: Path) -> dict:
    """Roda o MinerU localmente e retorna o structured_content.json."""
    with tempfile.TemporaryDirectory(prefix="mineru-") as tmp:
        cmd = [
            "mineru-kit", "parse", str(pdf_path),
            "-o", tmp, "--tier", MINERU_TIER, "-f", "zip",
            "--ocr-mode", choose_ocr_mode(pdf_path),
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=MINERU_TIMEOUT
        )
        if proc.returncode != 0:
            raise RuntimeError(f"mineru-kit falhou: {proc.stderr[-500:]}")

        zips = list(Path(tmp).rglob("*.zip"))
        if not zips:
            raise RuntimeError("mineru-kit não gerou saída")
        with zipfile.ZipFile(zips[0]) as z:
            return json.loads(z.read("structured_content.json"))


# ── Blocos ────────────────────────────────────────────────────────────────────

def _find_doi(*texts: str) -> str | None:
    for text in texts:
        m = DOI_PATTERN.search(text or "")
        if m:
            return m.group(0).rstrip(".,;)")
    return None


def parse_structured(content: dict) -> dict:
    """
    Converte a saída do MinerU em:
      {title, doi, abstract, references, blocks: [{page_number, section, text, atomic}]}
    """
    title, section, in_refs = None, None, False
    blocks: list[dict] = []
    references: list[str] = []
    doi_candidates = [content.get("metadata", {}).get("document", {}).get("title", "")]

    for page in content.get("pages", []):
        page_number = page["page_idx"] + 1
        for b in page.get("blocks", []):
            kind = b.get("type")
            text = (b.get("content") or "").replace("\x00", "").strip()

            if kind == "footer" and page_number == 1:
                doi_candidates.append(text)
            if kind in SKIP_TYPES:
                continue

            if kind == "doc_title":
                title = title or text
                continue
            if kind == "paragraph_title":
                section = text
                in_refs = bool(REFS_TITLE.match(text))
                continue
            if kind == "ref_text" or in_refs:
                if text:
                    references.append(text)
                continue

            atomic = False
            if kind == "equation":
                text, atomic = f"$$\n{text}\n$$", True
            elif "captions" in b:   # image, chart, table...
                parts = [text] + [c.get("content", "") for c in b.get("captions", [])]
                parts += [f.get("content", "") for f in b.get("footnotes", [])]
                text = "\n".join(p.strip() for p in parts if p and p.strip())
            if not text:
                continue

            # Parágrafo que continua da página anterior: junta ao bloco anterior
            if b.get("continues_prev") and blocks and not blocks[-1]["atomic"] and not atomic:
                blocks[-1]["text"] += " " + text
                continue

            blocks.append({
                "page_number": page_number,
                "section":     section,
                "text":        text,
                "atomic":      atomic,
            })

    abstract = "\n\n".join(
        b["text"] for b in blocks if (b["section"] or "").strip().lower() == "abstract"
    ) or None

    return {
        "title":      title,
        "doi":        _find_doi(*doi_candidates),
        "abstract":   abstract,
        "references": references,
        "blocks":     blocks,
    }


# ── Chunking por seção ────────────────────────────────────────────────────────

def _ntok(text: str) -> int:
    return len(tokenizer.encode(text))


def _split_long(block: dict) -> list[dict]:
    """Bloco maior que CHUNK_SIZE: quebra por tokens (equações ficam inteiras)."""
    if block["atomic"] or _ntok(block["text"]) <= CHUNK_SIZE:
        return [block]
    toks = tokenizer.encode(block["text"])
    step = CHUNK_SIZE - CHUNK_OVERLAP
    return [
        {**block, "text": tokenizer.decode(toks[i : i + CHUNK_SIZE]).strip()}
        for i in range(0, len(toks), step)
    ]


def chunk_blocks(blocks: list[dict]) -> list[dict]:
    """
    Agrupa blocos em chunks de até CHUNK_SIZE tokens, sem cruzar seções.
    Dentro de uma seção, o último bloco de um chunk é repetido no seguinte
    (se couber em CHUNK_OVERLAP) para preservar o contexto de equações.
    """
    chunks: list[dict] = []
    current: list[dict] = []

    def flush():
        if not current:
            return
        section = current[0]["section"]
        body = "\n\n".join(b["text"] for b in current)
        text = f"## {section}\n\n{body}" if section else body
        chunks.append({
            "chunk_index": len(chunks),
            "text":        text,
            "page_number": current[0]["page_number"],
            "section":     section,
            "token_count": _ntok(text),
        })

    for block in (piece for b in blocks for piece in _split_long(b)):
        same_section = current and current[0]["section"] == block["section"]
        size = sum(_ntok(b["text"]) for b in current)

        if current and (not same_section or size + _ntok(block["text"]) > CHUNK_SIZE):
            flush()
            last = current[-1]
            carry = same_section and len(current) > 1 and _ntok(last["text"]) <= CHUNK_OVERLAP
            current = [last] if carry else []
        current.append(block)

    flush()
    return chunks


# ── API ───────────────────────────────────────────────────────────────────────

def extract(pdf_path: Path) -> dict:
    """Retorna {title, doi, abstract, references, chunks} para um PDF."""
    doc = parse_structured(run_mineru(pdf_path))
    doc["chunks"] = chunk_blocks(doc.pop("blocks"))
    if not doc["chunks"]:
        raise RuntimeError("MinerU não extraiu nenhum conteúdo")
    return doc
