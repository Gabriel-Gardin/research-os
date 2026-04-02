"""
research-worker / ingest.py
Pipeline de ingestão de PDFs:
  1. Extrai texto com PyMuPDF
  2. Quebra em chunks (por tokens)
  3. Gera embeddings via OpenAI
  4. Salva no Postgres (deduplicação por SHA-256)
"""

import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Generator

import fitz  # pymupdf
import psycopg2
import tiktoken
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

# ── Configurações via env ─────────────────────────────────────────────────────
OPENAI_API_KEY   = os.environ["OPENAI_API_KEY"]
EMBEDDING_MODEL  = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM    = int(os.getenv("EMBEDDING_DIM", "1536"))
CHUNK_SIZE       = int(os.getenv("CHUNK_SIZE", "512"))     # tokens
CHUNK_OVERLAP    = int(os.getenv("CHUNK_OVERLAP", "64"))   # tokens
DATABASE_URL     = os.environ["DATABASE_URL"]

client   = OpenAI(api_key=OPENAI_API_KEY)
tokenizer = tiktoken.get_encoding("cl100k_base")


# ── Banco ─────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


# ── Extração de texto ─────────────────────────────────────────────────────────

def extract_pages(pdf_path: Path) -> list[dict]:
    """Retorna lista de {page_number, text} para cada página do PDF."""
    doc = fitz.open(str(pdf_path))
    pages = []
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text")
        # Fallback para OCR se página não tiver texto
        if not text.strip():
            tp = page.get_textpage_ocr(flags=0, language="por+eng", dpi=150)
            text = page.get_text("text", textpage=tp)
        if text.strip():
            pages.append({"page_number": i, "text": text})
    doc.close()
    return pages


# ── Chunking ──────────────────────────────────────────────────────────────────

def chunk_pages(pages: list[dict]) -> Generator[dict, None, None]:
    """
    Quebra o texto das páginas em chunks de CHUNK_SIZE tokens com CHUNK_OVERLAP.
    Cada chunk mantém referência à página de origem.
    """
    full_tokens: list[int] = []
    token_to_page: list[int] = []   # qual página cada token pertence

    for page in pages:
        toks = tokenizer.encode(page["text"])
        full_tokens.extend(toks)
        token_to_page.extend([page["page_number"]] * len(toks))

    step   = CHUNK_SIZE - CHUNK_OVERLAP
    idx    = 0
    c_idx  = 0

    while idx < len(full_tokens):
        end   = min(idx + CHUNK_SIZE, len(full_tokens))
        toks  = full_tokens[idx:end]
        text  = tokenizer.decode(toks)
        page  = token_to_page[idx]   # página do primeiro token do chunk

        yield {
            "chunk_index": c_idx,
            "text":        text.strip(),
            "page_number": page,
            "token_count": len(toks),
        }
        c_idx += 1
        idx   += step


# ── Embeddings ────────────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=30))
def embed_texts(texts: list[str]) -> list[list[float]]:
    """Gera embeddings em batch (máx 2048 inputs por chamada)."""
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
        dimensions=EMBEDDING_DIM,
    )
    return [item.embedding for item in response.data]


def embed_in_batches(texts: list[str], batch_size: int = 256) -> list[list[float]]:
    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        embeddings.extend(embed_texts(batch))
        time.sleep(0.1)   # gentileza com a API
    return embeddings


# ── Hash de arquivo ───────────────────────────────────────────────────────────

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


# ── Ingestão principal ────────────────────────────────────────────────────────

def ingest_pdf(pdf_path: Path, metadata: dict | None = None) -> str | None:
    """
    Ingesta um PDF completo.
    Retorna o document_id UUID ou None se já existia (deduplicação).
    """
    file_hash = sha256(pdf_path)
    meta = metadata or {}

    with get_conn() as conn, conn.cursor() as cur:
        # ── Deduplicação ───────────────────────────────────────────────────
        cur.execute("SELECT id FROM documents WHERE file_hash = %s", (file_hash,))
        row = cur.fetchone()
        if row:
            log.info(f"Documento já existe (hash={file_hash[:8]}…), pulando.")
            return None

        # ── Inserir documento ──────────────────────────────────────────────
        doc_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO documents (id, title, authors, year, type,
                                   source_path, file_hash, abstract,
                                   doi, tags, language, metadata)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                doc_id,
                meta.get("title", pdf_path.stem),
                meta.get("authors", []),
                meta.get("year"),
                meta.get("type", "paper"),
                str(pdf_path),
                file_hash,
                meta.get("abstract"),
                meta.get("doi"),
                meta.get("tags", []),
                meta.get("language", "en"),
                json.dumps(meta.get("extra", {})),
            ),
        )
        log.info(f"Documento criado: {doc_id} — {pdf_path.name}")

        # ── Extração + chunking ────────────────────────────────────────────
        pages  = extract_pages(pdf_path)
        chunks = list(chunk_pages(pages))
        log.info(f"  {len(pages)} páginas → {len(chunks)} chunks")

        # ── Embeddings ─────────────────────────────────────────────────────
        texts      = [c["text"] for c in chunks]
        embeddings = embed_in_batches(texts)
        log.info(f"  {len(embeddings)} embeddings gerados")

        # ── Inserir chunks ─────────────────────────────────────────────────
        for chunk, emb in zip(chunks, embeddings):
            cur.execute(
                """
                INSERT INTO chunks (document_id, chunk_index, text,
                                    page_number, token_count, embedding)
                VALUES (%s,%s,%s,%s,%s,%s::vector)
                """,
                (
                    doc_id,
                    chunk["chunk_index"],
                    chunk["text"].replace("\x00", ""),
                    chunk["page_number"],
                    chunk["token_count"],
                    json.dumps(emb),   # psycopg2 → pgvector aceita JSON array
                ),
            )

        conn.commit()
        log.info(f"  ✓ Ingestão completa: {len(chunks)} chunks salvos")
        return doc_id
