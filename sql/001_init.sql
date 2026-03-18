-- =============================================================================
-- Research OS – Schema inicial
-- Postgres + pgvector  |  text-embedding-3-small (1536 dims)
-- =============================================================================

-- Extensão vetorial
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- BIBLIOTECA DE CONHECIMENTO
-- =============================================================================

-- Documentos (papers, livros, teses, normas, notas, ...)
CREATE TABLE IF NOT EXISTS documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    authors     TEXT[]          DEFAULT '{}',
    year        INTEGER,
    type        TEXT            NOT NULL DEFAULT 'paper'
                    CHECK (type IN ('paper','book','thesis','norm','note','documentation','other')),
    source_path TEXT,                   -- caminho no Supabase Storage
    file_hash   TEXT UNIQUE,            -- SHA-256 para deduplicação
    abstract    TEXT,
    doi         TEXT,
    tags        TEXT[]          DEFAULT '{}',
    language    TEXT            DEFAULT 'en',
    metadata    JSONB           DEFAULT '{}',
    created_at  TIMESTAMPTZ     DEFAULT NOW(),
    updated_at  TIMESTAMPTZ     DEFAULT NOW()
);

-- Chunks de texto (unidades de busca semântica)
CREATE TABLE IF NOT EXISTS chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    page_number     INTEGER,
    section         TEXT,
    embedding       VECTOR(1536),       -- text-embedding-3-small
    token_count     INTEGER,
    metadata        JSONB           DEFAULT '{}',
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

-- =============================================================================
-- MEMÓRIA DE PESQUISA
-- =============================================================================

CREATE TABLE IF NOT EXISTS memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type        TEXT NOT NULL
                    CHECK (type IN (
                        'hypothesis',       -- hipótese a verificar
                        'conclusion',       -- conclusão estabelecida
                        'question',         -- dúvida em aberto
                        'decision',         -- decisão de projeto/metodologia
                        'observation',      -- observação experimental
                        'session_summary'   -- resumo de sessão de chat
                    )),
    title       TEXT,
    text        TEXT NOT NULL,
    status      TEXT            DEFAULT 'open'
                    CHECK (status IN ('open','confirmed','rejected','archived')),
    confidence  TEXT            DEFAULT 'medium'
                    CHECK (confidence IN ('low','medium','high')),
    embedding   VECTOR(1536),           -- para busca semântica na memória
    tags        TEXT[]          DEFAULT '{}',
    source      TEXT,                   -- 'chat', 'experiment', 'literature', etc.
    metadata    JSONB           DEFAULT '{}',
    created_at  TIMESTAMPTZ     DEFAULT NOW(),
    updated_at  TIMESTAMPTZ     DEFAULT NOW()
);

-- Vínculos entre memória e evidências (chunks de documentos)
CREATE TABLE IF NOT EXISTS evidence_links (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    chunk_id    UUID NOT NULL REFERENCES chunks(id)   ON DELETE CASCADE,
    relevance   TEXT DEFAULT 'supports'
                    CHECK (relevance IN ('supports','contradicts','related')),
    note        TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (memory_id, chunk_id)
);

-- =============================================================================
-- ÍNDICES
-- =============================================================================

-- Busca vetorial (IVFFlat – bom para coleções até ~500k chunks)
-- Ajuste lists conforme a coleção crescer: sqrt(n_rows) é uma boa heurística
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON memories USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);

-- Índices convencionais
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_documents_type     ON documents(type);
CREATE INDEX IF NOT EXISTS idx_documents_tags     ON documents USING gin(tags);
CREATE INDEX IF NOT EXISTS idx_memories_type      ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_status    ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_tags      ON memories USING gin(tags);
CREATE INDEX IF NOT EXISTS idx_evidence_memory    ON evidence_links(memory_id);
CREATE INDEX IF NOT EXISTS idx_evidence_chunk     ON evidence_links(chunk_id);

-- =============================================================================
-- FUNÇÕES DE BUSCA SEMÂNTICA
-- =============================================================================

-- Busca nos chunks da biblioteca
-- Uso: SELECT * FROM search_library('[0.1, 0.2, ...]'::vector, 10, 0.7);
CREATE OR REPLACE FUNCTION search_library(
    query_embedding VECTOR(1536),
    match_count     INTEGER DEFAULT 10,
    min_similarity  FLOAT   DEFAULT 0.5
)
RETURNS TABLE (
    chunk_id        UUID,
    document_id     UUID,
    document_title  TEXT,
    document_type   TEXT,
    authors         TEXT[],
    year            INTEGER,
    chunk_text      TEXT,
    page_number     INTEGER,
    section         TEXT,
    similarity      FLOAT
)
LANGUAGE sql STABLE AS $$
    SELECT
        c.id           AS chunk_id,
        d.id           AS document_id,
        d.title        AS document_title,
        d.type         AS document_type,
        d.authors,
        d.year,
        c.text         AS chunk_text,
        c.page_number,
        c.section,
        1 - (c.embedding <=> query_embedding) AS similarity
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.embedding IS NOT NULL
      AND 1 - (c.embedding <=> query_embedding) >= min_similarity
    ORDER BY c.embedding <=> query_embedding
    LIMIT match_count;
$$;

-- Busca na memória de pesquisa
CREATE OR REPLACE FUNCTION search_memories(
    query_embedding VECTOR(1536),
    match_count     INTEGER DEFAULT 10,
    min_similarity  FLOAT   DEFAULT 0.5,
    filter_type     TEXT    DEFAULT NULL,
    filter_status   TEXT    DEFAULT NULL
)
RETURNS TABLE (
    memory_id   UUID,
    type        TEXT,
    title       TEXT,
    text        TEXT,
    status      TEXT,
    confidence  TEXT,
    tags        TEXT[],
    similarity  FLOAT,
    created_at  TIMESTAMPTZ
)
LANGUAGE sql STABLE AS $$
    SELECT
        m.id,
        m.type,
        m.title,
        m.text,
        m.status,
        m.confidence,
        m.tags,
        1 - (m.embedding <=> query_embedding) AS similarity,
        m.created_at
    FROM memories m
    WHERE m.embedding IS NOT NULL
      AND (filter_type   IS NULL OR m.type   = filter_type)
      AND (filter_status IS NULL OR m.status = filter_status)
      AND 1 - (m.embedding <=> query_embedding) >= min_similarity
    ORDER BY m.embedding <=> query_embedding
    LIMIT match_count;
$$;

-- =============================================================================
-- TRIGGER: updated_at automático
-- =============================================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_documents_updated
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_memories_updated
    BEFORE UPDATE ON memories
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
