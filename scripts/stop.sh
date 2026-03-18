#!/usr/bin/env bash
# Research OS – Para todos os serviços
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[stop] Parando serviços de research..."
docker compose --project-name research -f docker-compose.research.yml down

echo "[stop] Parando Supabase..."
docker compose --project-name supabase -f supabase/docker/docker-compose.yml down

echo "[ok] Tudo parado. Dados persistidos em volumes/."
