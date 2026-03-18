#!/usr/bin/env bash
# =============================================================================
# Research OS – Bootstrap
# Configura o Supabase self-hosted + serviços de research
# =============================================================================
set -euo pipefail

CYAN='\033[0;36m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${CYAN}[setup]${NC} $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
success() { echo -e "${GREEN}[ok]${NC}    $*"; }
die()     { echo -e "${RED}[erro]${NC}  $*"; exit 1; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ── 1. Dependências ──────────────────────────────────────────────────────────
info "Verificando dependências..."
for cmd in docker git openssl; do
  command -v "$cmd" &>/dev/null || die "$cmd não encontrado. Instale e tente novamente."
done
docker compose version &>/dev/null || die "docker compose plugin não encontrado."
success "Dependências OK"

# ── 2. Supabase docker files ─────────────────────────────────────────────────
info "Baixando arquivos docker do Supabase..."
if [ ! -f "supabase/docker/docker-compose.yml" ]; then
  TMP=$(mktemp -d)
  git clone --filter=blob:none --sparse https://github.com/supabase/supabase "$TMP/supabase" -q
  cd "$TMP/supabase" && git sparse-checkout set docker && cd "$ROOT_DIR"
  cp -r "$TMP/supabase/docker/." supabase/docker/
  rm -rf "$TMP"
  success "Supabase docker files copiados para supabase/docker/"
else
  warn "supabase/docker/ já existe – pulando clone."
fi

# ── 3. .env ──────────────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
  info "Gerando .env com secrets aleatórios..."
  cp supabase/docker/.env.example .env

  gen32() { openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 40; }
  gen64() { openssl rand -base64 64 | tr -dc 'A-Za-z0-9' | head -c 64; }

  # Substitui placeholders do Supabase
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(gen32)/"       .env
  sed -i "s/^JWT_SECRET=.*/JWT_SECRET=$(gen64)/"                     .env
  sed -i "s/^ANON_KEY=.*/ANON_KEY=PLACEHOLDER_ANON/"                 .env
  sed -i "s/^SERVICE_ROLE_KEY=.*/SERVICE_ROLE_KEY=PLACEHOLDER_SRV/"  .env
  sed -i "s/^DASHBOARD_PASSWORD=.*/DASHBOARD_PASSWORD=$(gen32)/"     .env

  warn "ANON_KEY e SERVICE_ROLE_KEY precisam ser gerados com JWT_SECRET."
  warn "Consulte: https://supabase.com/docs/guides/self-hosting/docker#generate-api-keys"

  success ".env criado"
else
  warn ".env já existe – pulando."
fi

# ── 4. .env.research ─────────────────────────────────────────────────────────
if [ ! -f ".env.research" ]; then
  cp .env.research.example .env.research
  warn ".env.research criado a partir do exemplo. Preencha OPENAI_API_KEY."
else
  warn ".env.research já existe – pulando."
fi

# ── 5. Diretórios de dados ───────────────────────────────────────────────────
mkdir -p imports volumes/postgres volumes/storage
success "Diretórios criados"

echo ""
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}  Research OS – setup completo!           ${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
echo "  Próximos passos:"
echo "  1. Preencha .env.research com sua OPENAI_API_KEY"
echo "  2. Gere ANON_KEY / SERVICE_ROLE_KEY (ver aviso acima)"
echo "  3. ./scripts/start.sh"
echo ""
