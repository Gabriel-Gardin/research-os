"""
research-worker / watcher.py
Monitora a pasta IMPORTS_DIR e dispara ingestão para novos PDFs.
Também processa o que já existe ao iniciar.
"""

import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from rich.logging import RichHandler
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ingest import ingest_pdf

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True)],
)
log = logging.getLogger("watcher")

IMPORTS_DIR = Path(os.getenv("IMPORTS_DIR", "/imports"))
DONE_SUFFIX = ".ingested"   # marca arquivo já processado


def already_processed(path: Path) -> bool:
    return path.with_suffix(DONE_SUFFIX).exists()


def mark_done(path: Path):
    path.with_suffix(DONE_SUFFIX).touch()


def process(path: Path):
    if path.suffix.lower() != ".pdf":
        return
    if already_processed(path):
        log.debug(f"Já processado: {path.name}")
        return

    log.info(f"[+] Novo PDF detectado: {path.name}")

    # Metadados opcionais via arquivo .json com mesmo nome
    meta_path = path.with_suffix(".json")
    meta = {}
    if meta_path.exists():
        import json
        with open(meta_path) as f:
            meta = json.load(f)
        log.info(f"    Metadados carregados de {meta_path.name}")

    try:
        doc_id = ingest_pdf(path, metadata=meta)
        if doc_id:
            mark_done(path)
            log.info(f"    ✓ doc_id={doc_id}")
        else:
            mark_done(path)   # deduplicado, não tenta de novo
    except Exception as e:
        log.exception(f"    ✗ Falha ao ingestar {path.name}: {e}")


# ── Watchdog handler ──────────────────────────────────────────────────────────

class PDFHandler(FileSystemEventHandler):
    def on_created(self, event: FileCreatedEvent):
        if not event.is_directory:
            # Pequeno delay para garantir que o arquivo foi totalmente copiado
            time.sleep(1.5)
            process(Path(event.src_path))


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Research Worker iniciado. Monitorando: {IMPORTS_DIR}")

    # Processa arquivos que já existem
    log.info("Verificando PDFs existentes...")
    for pdf in sorted(IMPORTS_DIR.glob("*.pdf")):
        process(pdf)

    # Inicia monitoramento de novos arquivos
    handler  = PDFHandler()
    observer = Observer()
    observer.schedule(handler, str(IMPORTS_DIR), recursive=False)
    observer.start()
    log.info("Aguardando novos PDFs...")

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
