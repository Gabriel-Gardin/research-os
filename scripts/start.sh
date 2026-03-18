#!/usr/bin/env bash
# Research OS – Sobe todos os serviços
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[start] Subindo Supabase..."
docker compose \
  --project-name supabase \
  -f supabase/docker/docker-compose.yml \
  --env-file .env \
  up -d

echo "[start] Aguardando postgres ficar pronto..."
until docker exec supabase-db pg_isready -U postgres &>/dev/null; do
  sleep 1
done

echo "[start] Aplicando schema SQL..."
docker exec -i supabase-db psql -U postgres -d postgres \
  < sql/001_init.sql

echo "[start] Subindo serviços de research..."
docker compose \
  --project-name research \
  -f docker-compose.research.yml \
  --env-file .env \
  --env-file .env.research \
  up -d --build

echo ""
echo "[ok] Tudo no ar!"
echo "  Studio:        http://localhost:8000"
echo "  research-mcp:  localhost:8080"
echo "  Logs worker:   docker logs -f research-worker-1"
