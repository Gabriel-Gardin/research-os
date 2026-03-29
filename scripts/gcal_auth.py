#!/usr/bin/env python3
"""
scripts/gcal_auth.py
Script de autenticação OAuth2 com o Google Calendar.
Execute UMA VEZ na sua máquina local (fora do Docker).
Gera o token em secrets/gcal_token.json, que é montado no container.

Uso:
  pip install google-auth-oauthlib google-api-python-client
  python scripts/gcal_auth.py
"""

import json
import sys
from pathlib import Path

SECRETS_DIR = Path(__file__).parent.parent / "secrets"
CREDENTIALS_PATH = SECRETS_DIR / "gcal_credentials.json"
TOKEN_PATH = SECRETS_DIR / "gcal_token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main():
    SECRETS_DIR.mkdir(exist_ok=True)

    if not CREDENTIALS_PATH.exists():
        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  Arquivo de credenciais não encontrado.                          ║
║                                                                  ║
║  Passos para obter:                                              ║
║  1. Acesse: https://console.cloud.google.com/                    ║
║  2. Crie ou selecione um projeto                                 ║
║  3. APIs & Services → Enable APIs → Google Calendar API          ║
║  4. APIs & Services → Credentials → Create Credentials           ║
║     → OAuth client ID → Desktop App                              ║
║  5. Baixe o JSON e salve em:                                     ║
║     {str(CREDENTIALS_PATH):<54}║
╚══════════════════════════════════════════════════════════════════╝
""")
        sys.exit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Instale as dependências: pip install google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    print("Iniciando fluxo OAuth2...")
    print("Um navegador será aberto para você autorizar o acesso ao Google Calendar.\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_PATH.write_text(creds.to_json())
    print(f"\n✓ Token salvo em: {TOKEN_PATH}")
    print("  O research-mcp já pode criar eventos no seu Google Calendar.")
    print("\n  Rebuilde o container para aplicar:")
    print("  docker compose --project-name research -f docker-compose.research.yml \\")
    print("    --env-file .env --env-file .env.research up -d --build research-mcp")


if __name__ == "__main__":
    main()
