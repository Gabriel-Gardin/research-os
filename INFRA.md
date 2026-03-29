# Research OS – Guia de Infraestrutura

## Pré-requisitos

- Docker Engine + Docker Compose plugin (`docker compose version`)
- Git
- Chave de API da OpenAI

---

## Primeira vez: setup completo

```bash
# 1. Clone este repositório
git clone <seu-repo> research-os && cd research-os

# 2. Torne os scripts executáveis
chmod +x scripts/*.sh

# 3. Bootstrap (baixa Supabase docker, gera .env com secrets)
./scripts/setup.sh

# 4. Preencha sua OpenAI API key
echo "OPENAI_API_KEY=sk-..." >> .env.research

# 5. Gere ANON_KEY e SERVICE_ROLE_KEY
#    Acesse: https://supabase.com/docs/guides/self-hosting/docker#generate-api-keys
#    Use o JWT_SECRET que está no .env gerado.
nano .env   # edite ANON_KEY e SERVICE_ROLE_KEY

# 6. Sobe tudo
./scripts/start.sh

# 7. Drop dos índices vetoriais (necessário apenas na primeira vez)
docker exec supabase-db psql -U postgres -d postgres << 'SQL'
DROP INDEX IF EXISTS idx_chunks_embedding;
DROP INDEX IF EXISTS idx_memories_embedding;
SQL
```

Acesse o Supabase Studio: **http://localhost:8000**
(usuário: `supabase`, senha: valor de `DASHBOARD_PASSWORD` no `.env`)

---

## Uso diário

```bash
./scripts/start.sh   # sobe
./scripts/stop.sh    # para (dados persistidos)
```

---

## Ingestão de PDFs

Coloque PDFs na pasta `imports/`. O worker detecta automaticamente e ingesta.

```
imports/
  meu-artigo.pdf
  meu-artigo.json   ← opcional: metadados
```

**Formato do `.json` de metadados:**
```json
{
  "title": "Acoustic leak detection in water pipes",
  "authors": ["Hunaidi, O.", "Chu, W.T."],
  "year": 1999,
  "type": "paper",
  "doi": "10.1016/S0963-8695(99)00009-4",
  "tags": ["correlação acústica", "detecção de vazamentos", "tubulações"],
  "language": "en"
}
```

**Acompanhar ingestão:**
```bash
docker logs -f research-worker
```

---

## Conectar o MCP ao Claude

### Claude Code CLI (recomendado para uso local no Ubuntu)

Com o `research-mcp` no ar, registre o servidor uma única vez com escopo global:

```bash
claude mcp add --transport sse --scope user research-os http://localhost:8080/sse
```

Verifique se foi registrado:

```bash
claude mcp list
```

Abra o Claude Code normalmente:

```bash
claude
```

As tools aparecem listadas como `mcp__research-os__*`. O registro é persistente — não é necessário repetir o `mcp add` nas próximas sessões. Basta garantir que `./scripts/start.sh` foi rodado antes de abrir o Claude Code.

**Importante:** para lembretes via Google Calendar, sempre use a tool `create_reminder` do MCP explicitamente — nunca use `CronCreate` ou outros mecanismos nativos do Claude Code, pois eles não persistem entre sessões e não integram com o Google Calendar.

### Claude.ai (remote MCP via SSE)

Configure um túnel público (ex: ngrok) e adicione em Claude.ai > Settings > Integrations:

```
URL: http://localhost:8080/sse
```

---

## Integração com Google Calendar (lembretes)

A tool `create_reminder` cria eventos no Google Calendar com alertas. O setup requer autenticação OAuth2 feita uma única vez.

### 1. Criar credenciais no Google Cloud Console

- Acesse [console.cloud.google.com](https://console.cloud.google.com)
- APIs & Services → Enable APIs → **Google Calendar API**
- APIs & Services → Credentials → Create Credentials → **OAuth client ID** → Desktop App
- Baixe o JSON e salve em `secrets/gcal_credentials.json`

### 2. Adicionar seu email como tester

O app OAuth começa em modo de teste e só aceita usuários autorizados:

- APIs & Services → **OAuth consent screen**
- Role até **Test users** → **Add users**
- Adicione o email da sua conta Google
- Salve

### 3. Gerar o token de autenticação (uma vez)

```bash
python3 -m venv /tmp/gcal-auth-venv
/tmp/gcal-auth-venv/bin/pip install google-auth-oauthlib google-api-python-client
/tmp/gcal-auth-venv/bin/python scripts/gcal_auth.py
```

Um navegador abrirá para autorização. O token será salvo em `secrets/gcal_token.json`.

### 4. Restartar o research-mcp

```bash
docker restart research-mcp
```

### Uso no Claude Code

```
Use a tool create_reminder do research-os para me lembrar de estudar propagação de ondas
na próxima segunda às 09:00
```

Formatos de data aceitos: `amanha 10:00`, `proxima segunda 09:00`,
`semana que vem quinta 14:00` ou `2026-04-15 09:00`.

O lembrete é criado no Google Calendar **e** salvo na memória de pesquisa automaticamente.

---

## Banco de dados

| Tabela           | Conteúdo                                        |
|------------------|-------------------------------------------------|
| `documents`      | Metadados dos documentos ingeridos              |
| `chunks`         | Trechos de texto com embeddings (1536 dims)     |
| `memories`       | Hipóteses, conclusões, dúvidas, resumos         |
| `evidence_links` | Relação memória ↔ chunk de evidência            |

**Busca manual no Studio:**
```sql
-- Busca semântica (substitua o vetor por um embedding real)
SELECT document_title, chunk_text, similarity
FROM search_library('[0.1, 0.2, ...]'::vector, 5, 0.5);

-- Dúvidas em aberto
SELECT title, text, created_at FROM memories
WHERE type = 'question' AND status = 'open'
ORDER BY created_at DESC;
```

---

## Índices vetoriais

O schema cria índices IVFFlat para busca vetorial, mas eles exigem um mínimo de linhas para funcionar corretamente. Se você rodar pela primeira vez com poucos documentos, os índices precisam ser dropados para o Postgres usar sequential scan automaticamente (já incluído no passo 7 do setup).

Quando a biblioteca crescer, recriar os índices melhora a performance:

| Tamanho da coleção | Ação recomendada |
|--------------------|------------------|
| < 1.000 chunks (~30 artigos) | Sem índice (sequential scan) |
| ~10.000 chunks (~300 artigos) | Recriar com `lists = 100` |
| ~100.000 chunks (~3000 artigos) | Recriar com `lists = 300` |

**Comando para recriar quando necessário:**
```sql
CREATE INDEX idx_chunks_embedding
  ON chunks USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

CREATE INDEX idx_memories_embedding
  ON memories USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 50);
```

---

## Estrutura do repositório

```
research-os/
├─ scripts/
│  ├─ setup.sh                  ← bootstrap (roda uma vez)
│  ├─ start.sh                  ← sobe tudo
│  ├─ stop.sh                   ← para tudo
│  └─ gcal_auth.py              ← autenticação OAuth Google Calendar (roda uma vez)
├─ supabase/docker/             ← gerado pelo setup.sh (git-ignored)
├─ docker-compose.research.yml  ← worker + mcp
├─ .env.research.example        ← template de vars do research
├─ secrets/                     ← tokens OAuth e credenciais (git-ignored)
│  ├─ gcal_credentials.json     ← baixado do Google Cloud Console
│  └─ gcal_token.json           ← gerado pelo gcal_auth.py
├─ sql/
│  └─ 001_init.sql              ← schema: tabelas, índices, funções
├─ imports/                     ← coloque PDFs aqui
├─ research-worker/
│  ├─ Dockerfile
│  ├─ requirements.txt
│  ├─ watcher.py                ← monitora imports/
│  └─ ingest.py                 ← pipeline de extração/embedding
└─ research-mcp/
   ├─ Dockerfile
   ├─ requirements.txt
   ├─ server.py                 ← MCP tools
   └─ gcal.py                   ← integração Google Calendar
```

---

## MCP Tools disponíveis

| Tool                   | Descrição                                          |
|------------------------|----------------------------------------------------|
| `search_library`       | Busca semântica nos documentos                     |
| `get_passage`          | Recupera trecho específico por ID                  |
| `list_documents`       | Lista documentos com filtros                       |
| `save_hypothesis`      | Salva hipótese na memória                          |
| `save_conclusion`      | Salva conclusão na memória                         |
| `save_question`        | Salva dúvida em aberto                             |
| `save_session_summary` | Salva resumo de sessão de pesquisa                 |
| `get_project_memory`   | Busca semântica na memória de pesquisa             |
| `list_open_questions`  | Lista dúvidas em aberto                            |
| `update_memory_status` | Atualiza status de memória (open/confirmed/etc.)   |
| `create_reminder`      | Cria lembrete no Google Calendar + memória         |
