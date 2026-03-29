"""
research-mcp / server.py
Servidor MCP que expõe a biblioteca de conhecimento e a memória de pesquisa
para uso por LLMs (Claude Desktop, Claude.ai remote MCP, etc.).

Tools implementadas:
  search_library        – busca semântica nos chunks de documentos
  get_passage           – recupera trecho por document_id + chunk_index
  save_hypothesis       – salva hipótese nova
  save_conclusion       – salva conclusão nova
  save_question         – salva dúvida em aberto
  save_session_summary  – salva resumo de sessão de pesquisa
  get_project_memory    – busca semântica nas memórias
  list_open_questions   – lista todas as dúvidas em aberto
  update_memory_status  – atualiza status de uma memória
  list_documents        – lista documentos na biblioteca (com filtros)
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import psycopg2
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL    = os.environ["DATABASE_URL"]
OPENAI_API_KEY  = os.environ["OPENAI_API_KEY"]
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM   = int(os.getenv("EMBEDDING_DIM", "1536"))
MCP_PORT        = int(os.getenv("MCP_PORT", "8080"))
MCP_TRANSPORT   = os.getenv("MCP_TRANSPORT", "sse")   # "sse" | "stdio"

openai_client = OpenAI(api_key=OPENAI_API_KEY)
mcp = FastMCP("Research OS", port=MCP_PORT)


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def embed(text: str) -> list[float]:
    resp = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[text],
        dimensions=EMBEDDING_DIM,
    )
    return resp.data[0].embedding


def vec_literal(embedding: list[float]) -> str:
    """Converte lista para literal de vetor do pgvector."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS – BIBLIOTECA
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def search_library(
    query: str,
    match_count: int = 8,
    min_similarity: float = 0.45,
    doc_type: str | None = None,
) -> list[dict]:
    """
    Busca semântica nos documentos da biblioteca de conhecimento.
    Retorna chunks relevantes com título, autores, ano e trecho de texto.

    Args:
        query:          Texto ou pergunta para busca semântica.
        match_count:    Número máximo de resultados (default 8).
        min_similarity: Limiar de similaridade cosseno (0–1, default 0.45).
        doc_type:       Filtrar por tipo: 'paper','book','thesis','norm','note'.
    """
    emb = embed(query)

    with get_conn() as conn, conn.cursor() as cur:
        if doc_type:
            cur.execute(
                """
                SELECT chunk_id, document_id, document_title, document_type,
                       authors, year, chunk_text, page_number, section, similarity
                FROM search_library(%s::vector, %s, %s)
                WHERE document_type = %s
                ORDER BY similarity DESC
                """,
                (vec_literal(emb), match_count, min_similarity, doc_type),
            )
        else:
            cur.execute(
                """
                SELECT chunk_id, document_id, document_title, document_type,
                       authors, year, chunk_text, page_number, section, similarity
                FROM search_library(%s::vector, %s, %s)
                ORDER BY similarity DESC
                """,
                (vec_literal(emb), match_count, min_similarity),
            )

        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    # Converte UUIDs para string
    for r in rows:
        r["chunk_id"]    = str(r["chunk_id"])
        r["document_id"] = str(r["document_id"])
        r["similarity"]  = round(float(r["similarity"]), 4)

    return rows


@mcp.tool()
def get_passage(document_id: str, chunk_index: int) -> dict:
    """
    Recupera um trecho específico da biblioteca pelo document_id e índice do chunk.
    Útil para expandir um resultado do search_library.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.text, c.page_number, c.section,
                   d.title, d.authors, d.year, d.doi
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.document_id = %s AND c.chunk_index = %s
            """,
            (document_id, chunk_index),
        )
        row = cur.fetchone()

    if not row:
        return {"error": "Trecho não encontrado."}

    return {
        "text":        row[0],
        "page_number": row[1],
        "section":     row[2],
        "title":       row[3],
        "authors":     row[4],
        "year":        row[5],
        "doi":         row[6],
    }


@mcp.tool()
def list_documents(
    doc_type: str | None = None,
    tag: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """
    Lista documentos na biblioteca com filtros opcionais.

    Args:
        doc_type: Filtrar por tipo ('paper','book','thesis','norm','note').
        tag:      Filtrar por tag.
        limit:    Máximo de resultados (default 50).
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, authors, year, type, tags, doi, created_at
            FROM documents
            WHERE (%s IS NULL OR type = %s)
              AND (%s IS NULL OR %s = ANY(tags))
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (doc_type, doc_type, tag, tag, limit),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    for r in rows:
        r["id"]         = str(r["id"])
        r["created_at"] = str(r["created_at"])

    return rows


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS – MEMÓRIA
# ══════════════════════════════════════════════════════════════════════════════

def _save_memory(
    memory_type: str,
    text: str,
    title: str | None,
    tags: list[str],
    confidence: str,
    source: str,
    evidence_chunk_ids: list[str],
) -> dict:
    """Função interna que salva qualquer tipo de memória."""
    emb = embed(text)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO memories (type, title, text, confidence, tags, source, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
            RETURNING id, created_at
            """,
            (memory_type, title, text, confidence, tags, source, vec_literal(emb)),
        )
        row = cur.fetchone()
        memory_id = str(row[0])
        created_at = str(row[1])

        # Vínculo com evidências
        for chunk_id in (evidence_chunk_ids or []):
            try:
                cur.execute(
                    """
                    INSERT INTO evidence_links (memory_id, chunk_id, relevance)
                    VALUES (%s, %s, 'supports')
                    ON CONFLICT DO NOTHING
                    """,
                    (memory_id, chunk_id),
                )
            except Exception:
                pass  # chunk_id inválido, não bloqueia

        conn.commit()

    return {"memory_id": memory_id, "type": memory_type, "created_at": created_at}


@mcp.tool()
def save_hypothesis(
    text: str,
    title: str | None = None,
    tags: list[str] = [],
    confidence: str = "medium",
    evidence_chunk_ids: list[str] = [],
) -> dict:
    """
    Salva uma hipótese na memória de pesquisa.

    Args:
        text:               Texto completo da hipótese.
        title:              Título curto opcional.
        tags:               Tags para organização (ex: ['correlação','ruído']).
        confidence:         'low' | 'medium' | 'high'
        evidence_chunk_ids: IDs de chunks que evidenciam esta hipótese.
    """
    return _save_memory("hypothesis", text, title, tags, confidence, "chat", evidence_chunk_ids)


@mcp.tool()
def save_conclusion(
    text: str,
    title: str | None = None,
    tags: list[str] = [],
    confidence: str = "high",
    evidence_chunk_ids: list[str] = [],
) -> dict:
    """
    Salva uma conclusão estabelecida na memória de pesquisa.

    Args:
        text:               Texto completo da conclusão.
        title:              Título curto opcional.
        tags:               Tags para organização.
        confidence:         'low' | 'medium' | 'high'
        evidence_chunk_ids: IDs de chunks que suportam esta conclusão.
    """
    return _save_memory("conclusion", text, title, tags, confidence, "chat", evidence_chunk_ids)


@mcp.tool()
def save_question(
    text: str,
    title: str | None = None,
    tags: list[str] = [],
) -> dict:
    """
    Salva uma dúvida ou questão em aberto na memória de pesquisa.

    Args:
        text:  Descrição completa da dúvida.
        title: Título curto opcional.
        tags:  Tags para organização.
    """
    return _save_memory("question", text, title, tags, "medium", "chat", [])


@mcp.tool()
def save_session_summary(
    text: str,
    title: str | None = None,
    tags: list[str] = [],
) -> dict:
    """
    Salva um resumo de sessão de pesquisa/chat.
    Use ao final de conversas relevantes para preservar raciocínio.

    Args:
        text:  Resumo da sessão (principais discussões, decisões, insights).
        title: Título descritivo (ex: 'Sessão: ruído correlacionado em hidrofones').
        tags:  Tags temáticas.
    """
    return _save_memory("session_summary", text, title, tags, "medium", "chat", [])


@mcp.tool()
def get_project_memory(
    query: str,
    match_count: int = 10,
    memory_type: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """
    Busca semântica na memória de pesquisa (hipóteses, conclusões, dúvidas, etc.).

    Args:
        query:        Texto ou pergunta para busca semântica.
        match_count:  Número máximo de resultados (default 10).
        memory_type:  Filtrar por tipo: 'hypothesis','conclusion','question',
                      'decision','observation','session_summary'.
        status:       Filtrar por status: 'open','confirmed','rejected','archived'.
    """
    emb = embed(query)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT memory_id, type, title, text, status,
                   confidence, tags, similarity, created_at
            FROM search_memories(%s::vector, %s, 0.4, %s, %s)
            ORDER BY similarity DESC
            """,
            (vec_literal(emb), match_count, memory_type, status),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    for r in rows:
        r["memory_id"]  = str(r["memory_id"])
        r["created_at"] = str(r["created_at"])
        r["similarity"] = round(float(r["similarity"]), 4)

    return rows


@mcp.tool()
def list_open_questions(limit: int = 30) -> list[dict]:
    """
    Lista todas as dúvidas em aberto na memória de pesquisa,
    ordenadas da mais recente para a mais antiga.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, text, tags, created_at
            FROM memories
            WHERE type = 'question' AND status = 'open'
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    for r in rows:
        r["id"]         = str(r["id"])
        r["created_at"] = str(r["created_at"])

    return rows


@mcp.tool()
def update_memory_status(memory_id: str, status: str, note: str | None = None) -> dict:
    """
    Atualiza o status de uma memória (ex: marcar hipótese como confirmada).

    Args:
        memory_id: UUID da memória.
        status:    'open' | 'confirmed' | 'rejected' | 'archived'
        note:      Observação opcional a adicionar nos metadados.
    """
    valid = {"open", "confirmed", "rejected", "archived"}
    if status not in valid:
        return {"error": f"Status inválido. Use: {valid}"}

    with get_conn() as conn, conn.cursor() as cur:
        if note:
            cur.execute(
                """
                UPDATE memories
                SET status = %s,
                    metadata = metadata || jsonb_build_object('status_note', %s)
                WHERE id = %s
                RETURNING id, type, status
                """,
                (status, note, memory_id),
            )
        else:
            cur.execute(
                "UPDATE memories SET status = %s WHERE id = %s RETURNING id, type, status",
                (status, memory_id),
            )
        row = cur.fetchone()
        conn.commit()

    if not row:
        return {"error": "Memória não encontrada."}

    return {"memory_id": str(row[0]), "type": row[1], "status": row[2]}


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS – LEMBRETES (Google Calendar)
# ══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def create_reminder(
    title: str,
    description: str,
    when: str,
    duration_minutes: int = 30,
    reminders_minutes: list[int] = [30, 10],
    save_to_memory: bool = True,
) -> dict:
    """
    Cria um lembrete/evento no Google Calendar.
    Opcionalmente salva também na memória de pesquisa.

    Args:
        title:              Título do lembrete (ex: 'Estudar tópico XYZ').
        description:        Descrição detalhada do que fazer.
        when:               Data e hora no formato 'YYYY-MM-DD HH:MM'
                            ou relativo como 'amanha 10:00', 'semana que vem segunda 09:00'.
        duration_minutes:   Duração do evento em minutos (default: 30).
        reminders_minutes:  Lista de antecedência dos alertas em minutos (default: [30, 10]).
        save_to_memory:     Se True, salva também como memória do tipo 'decision' (default: True).
    """
    from gcal import create_event

    TZ = ZoneInfo("America/Sao_Paulo")

    # ── Parse de data/hora ────────────────────────────────────────────────────
    now = datetime.now(TZ)

    when_lower = when.strip().lower()
    start_dt = None

    # Relativo: "amanha HH:MM"
    if when_lower.startswith("amanha") or when_lower.startswith("amanhã"):
        parts = when_lower.split()
        time_str = parts[1] if len(parts) > 1 else "09:00"
        h, m = map(int, time_str.split(":"))
        start_dt = (now + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)

    # Relativo: "semana que vem WEEKDAY HH:MM"
    elif "semana que vem" in when_lower:
        parts = when_lower.replace("semana que vem", "").strip().split()
        weekday_map = {
            "segunda": 0, "terca": 1, "terça": 1,
            "quarta": 2, "quinta": 3, "sexta": 4,
            "sabado": 5, "sábado": 5, "domingo": 6,
        }
        weekday = weekday_map.get(parts[0], 0) if parts else 0
        time_str = parts[1] if len(parts) > 1 else "09:00"
        h, m = map(int, time_str.split(":"))
        days_ahead = (weekday - now.weekday() + 7) % 7 or 7
        start_dt = (now + timedelta(days=days_ahead + 7)).replace(
            hour=h, minute=m, second=0, microsecond=0
        )

    # Relativo: "proxima segunda HH:MM" / "próxima segunda 09:00"
    elif "proxima" in when_lower or "próxima" in when_lower:
        parts = when_lower.replace("proxima", "").replace("próxima", "").strip().split()
        weekday_map = {
            "segunda": 0, "terca": 1, "terça": 1,
            "quarta": 2, "quinta": 3, "sexta": 4,
            "sabado": 5, "sábado": 5, "domingo": 6,
        }
        weekday = weekday_map.get(parts[0], 0) if parts else 0
        time_str = parts[1] if len(parts) > 1 else "09:00"
        h, m = map(int, time_str.split(":"))
        days_ahead = (weekday - now.weekday() + 7) % 7 or 7
        start_dt = (now + timedelta(days=days_ahead)).replace(
            hour=h, minute=m, second=0, microsecond=0
        )

    # Absoluto: "YYYY-MM-DD HH:MM"
    else:
        try:
            start_dt = datetime.strptime(when.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        except ValueError:
            return {
                "error": (
                    f"Formato de data não reconhecido: '{when}'. "
                    "Use 'YYYY-MM-DD HH:MM', 'amanha HH:MM', "
                    "'proxima segunda HH:MM' ou 'semana que vem segunda HH:MM'."
                )
            }

    end_dt = start_dt + timedelta(minutes=duration_minutes)

    # ── Cria evento no Google Calendar ────────────────────────────────────────
    try:
        event = create_event(
            title=title,
            description=description,
            start_dt=start_dt,
            end_dt=end_dt,
            reminders_minutes=reminders_minutes,
        )
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Erro ao criar evento: {e}"}

    result = {
        "status":   "criado",
        "title":    event["title"],
        "start":    event["start"],
        "end":      event["end"],
        "link":     event["link"],
        "event_id": event["event_id"],
    }

    # ── Salva na memória de pesquisa ──────────────────────────────────────────
    if save_to_memory:
        memory_text = (
            f"Lembrete criado: {title}\n"
            f"Agendado para: {event['start']}\n"
            f"Descrição: {description}"
        )
        _save_memory(
            memory_type="decision",
            text=memory_text,
            title=f"[Lembrete] {title}",
            tags=["lembrete", "agenda"],
            confidence="high",
            source="chat",
            evidence_chunk_ids=[],
        )
        result["saved_to_memory"] = True

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Entrypoint
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    log.info(f"Research MCP iniciando — transport={MCP_TRANSPORT}, port={MCP_PORT}")

    if MCP_TRANSPORT == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="sse")
