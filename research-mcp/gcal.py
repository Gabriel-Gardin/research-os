"""
research-mcp / gcal.py
Integração com Google Calendar via OAuth2.
O token é gerado uma vez pelo script scripts/gcal_auth.py e montado no container.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_PATH = Path(os.getenv("GCAL_TOKEN_PATH", "/secrets/gcal_token.json"))
CREDENTIALS_PATH = Path(os.getenv("GCAL_CREDENTIALS_PATH", "/secrets/gcal_credentials.json"))
DEFAULT_CALENDAR_ID = os.getenv("GCAL_CALENDAR_ID", "primary")


def get_service():
    """Retorna um cliente autenticado do Google Calendar."""
    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"Token OAuth não encontrado em {TOKEN_PATH}. "
            "Execute scripts/gcal_auth.py para autenticar."
        )

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    # Renova token se expirado
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
        log.info("Token do Google Calendar renovado.")

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def create_event(
    title: str,
    description: str,
    start_dt: datetime,
    end_dt: datetime,
    calendar_id: str = DEFAULT_CALENDAR_ID,
    reminders_minutes: list[int] | None = None,
) -> dict:
    """
    Cria um evento no Google Calendar.
    Retorna o evento criado com id e htmlLink.
    """
    service = get_service()

    event_body = {
        "summary": title,
        "description": description,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "America/Sao_Paulo",
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "America/Sao_Paulo",
        },
    }

    if reminders_minutes:
        event_body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": m} for m in reminders_minutes
            ],
        }
    else:
        event_body["reminders"] = {"useDefault": True}

    try:
        event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        return {
            "event_id":   event["id"],
            "title":      event["summary"],
            "start":      event["start"]["dateTime"],
            "end":        event["end"]["dateTime"],
            "link":       event.get("htmlLink"),
            "calendar_id": calendar_id,
        }
    except HttpError as e:
        log.error(f"Erro ao criar evento no Google Calendar: {e}")
        raise
