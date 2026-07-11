"""Gmail e Google Calendar via APIs oficiais do Google (OAuth).

Autenticação: rode `python scripts/google_auth.py` uma vez (no Mac, com
navegador) para gerar state/google_token.json — veja DEPLOY.md.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import Tool, ToolContext, truncate
from ..config import ROOT

STATE_DIR = ROOT / "state"
CREDENTIALS_FILE = STATE_DIR / "google_credentials.json"
TOKEN_FILE = STATE_DIR / "google_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def has_credentials() -> bool:
    return TOKEN_FILE.exists()


def _service(name: str, version: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return build(name, version, credentials=creds, cache_discovery=False)


# ---- Gmail ----


def _list_emails_sync(query: str, max_results: int) -> str:
    gmail = _service("gmail", "v1")
    resp = gmail.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = resp.get("messages", [])
    if not messages:
        return f"Nenhum email encontrado para a busca: {query!r}"
    lines = []
    for ref in messages:
        msg = (
            gmail.users()
            .messages()
            .get(userId="me", id=ref["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        lines.append(
            f"id: {ref['id']}\n"
            f"  de: {headers.get('From', '?')}\n"
            f"  assunto: {headers.get('Subject', '(sem assunto)')}\n"
            f"  resumo: {msg.get('snippet', '')[:150]}"
        )
    return truncate("\n".join(lines))


def _read_email_sync(message_id: str) -> str:
    gmail = _service("gmail", "v1")
    msg = gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}

    def extract_text(part) -> str:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode(errors="replace")
        return "".join(extract_text(p) for p in part.get("parts", []))

    body = extract_text(msg["payload"]) or msg.get("snippet", "(sem corpo de texto)")
    return truncate(
        f"De: {headers.get('From', '?')}\n"
        f"Assunto: {headers.get('Subject', '(sem assunto)')}\n"
        f"Data: {headers.get('Date', '?')}\n\n{body}"
    )


# ---- Calendar ----


def _calendar_events_sync(days: int, timezone: str) -> str:
    calendar = _service("calendar", "v3")
    now = datetime.now(ZoneInfo(timezone))
    resp = (
        calendar.events()
        .list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=days)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=30,
        )
        .execute()
    )
    events = resp.get("items", [])
    if not events:
        return f"Nenhum compromisso nos próximos {days} dia(s)."
    lines = []
    for ev in events:
        start = ev["start"].get("dateTime", ev["start"].get("date", "?"))
        location = f" — {ev['location']}" if ev.get("location") else ""
        lines.append(f"{start}: {ev.get('summary', '(sem título)')}{location}")
    return truncate("\n".join(lines))


def _create_event_sync(summary: str, start_iso: str, end_iso: str, timezone: str, description: str) -> str:
    calendar = _service("calendar", "v3")
    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_iso, "timeZone": timezone},
        "end": {"dateTime": end_iso, "timeZone": timezone},
    }
    created = calendar.events().insert(calendarId="primary", body=event).execute()
    return f"Compromisso criado: {summary} ({start_iso}). Link: {created.get('htmlLink', '')}"


# ---- ferramentas (as libs do Google são síncronas → rodam em thread) ----


async def _list_emails(ctx: ToolContext, query: str = "is:unread category:primary", max_results: int = 10) -> str:
    return await asyncio.to_thread(_list_emails_sync, query, int(max_results))


async def _read_email(ctx: ToolContext, message_id: str) -> str:
    return await asyncio.to_thread(_read_email_sync, message_id)


async def _calendar_events(ctx: ToolContext, days: int = 1) -> str:
    return await asyncio.to_thread(_calendar_events_sync, int(days), ctx.config.timezone)


async def _create_event(ctx: ToolContext, summary: str, start_iso: str, end_iso: str, description: str = "") -> str:
    return await asyncio.to_thread(
        _create_event_sync, summary, start_iso, end_iso, ctx.config.timezone, description
    )


LIST_EMAILS_TOOL = Tool(
    declaration={
        "name": "list_emails",
        "description": (
            "Lista emails do Gmail do chefe. Por padrão, os não lidos da caixa principal. "
            "Aceita sintaxe de busca do Gmail (ex.: 'from:fulano', 'newer_than:2d')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Busca Gmail. Padrão: is:unread category:primary"},
                "max_results": {"type": "INTEGER", "description": "Máximo de emails (padrão 10)."},
            },
        },
    },
    handler=_list_emails,
)

READ_EMAIL_TOOL = Tool(
    declaration={
        "name": "read_email",
        "description": "Lê o conteúdo completo de um email pelo id retornado por list_emails.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message_id": {"type": "STRING", "description": "Id do email."}
            },
            "required": ["message_id"],
        },
    },
    handler=_read_email,
)

CALENDAR_TOOL = Tool(
    declaration={
        "name": "calendar_events",
        "description": "Lista os compromissos da agenda (Google Calendar) dos próximos dias.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {"type": "INTEGER", "description": "Quantos dias à frente olhar (padrão 1 = hoje)."}
            },
        },
    },
    handler=_calendar_events,
)

CREATE_EVENT_TOOL = Tool(
    declaration={
        "name": "create_calendar_event",
        "description": (
            "Cria um compromisso na agenda do chefe. Calcule horários absolutos "
            "a partir do horário atual do contexto."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "summary": {"type": "STRING", "description": "Título do compromisso."},
                "start_iso": {"type": "STRING", "description": "Início, ISO: AAAA-MM-DDTHH:MM:SS"},
                "end_iso": {"type": "STRING", "description": "Fim, ISO: AAAA-MM-DDTHH:MM:SS"},
                "description": {"type": "STRING", "description": "Detalhes (opcional)."},
            },
            "required": ["summary", "start_iso", "end_iso"],
        },
    },
    handler=_create_event,
)
