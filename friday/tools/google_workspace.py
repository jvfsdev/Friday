"""Gmail e Google Calendar via APIs oficiais do Google (OAuth), multi-contas.

Cada conta autorizada gera um token nomeado (state/google_token_<nome>.json).
Autorização: `python scripts/google_auth.py [nome]` numa máquina com navegador
(padrão: "principal") — veja DEPLOY.md.
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
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
]


def token_file(account: str):
    return STATE_DIR / f"google_token_{account}.json"


def accounts() -> list[str]:
    return sorted(p.stem.removeprefix("google_token_") for p in STATE_DIR.glob("google_token_*.json"))


def has_credentials() -> bool:
    return bool(accounts())


def _pick(account: str) -> list[str] | str:
    """Resolve o parâmetro `account` para uma lista de contas, ou erro legível."""
    available = accounts()
    if account:
        if account in available:
            return [account]
        return f"Conta Google '{account}' não existe. Contas conectadas: {', '.join(available)}."
    return available


def _pick_one(account: str) -> str:
    """Para ações que exigem exatamente uma conta (criar evento, ler email)."""
    available = accounts()
    if account:
        return account if account in available else ""
    return available[0] if len(available) == 1 else ""


def _service(name: str, version: str, account: str):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    path = token_file(account)
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json(), encoding="utf-8")
    return build(name, version, credentials=creds, cache_discovery=False)


# ---- Gmail ----


def _list_emails_sync(account: str, query: str, max_results: int) -> str:
    gmail = _service("gmail", "v1", account)
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
    return "\n".join(lines)


def _read_email_sync(account: str, message_id: str) -> str:
    gmail = _service("gmail", "v1", account)
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


def _send_email_sync(account: str, to: str, subject: str, body: str) -> str:
    from email.mime.text import MIMEText

    gmail = _service("gmail", "v1", account)
    mime = MIMEText(body, "plain", "utf-8")
    mime["To"] = to
    mime["Subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
    return f"Email enviado para {to} pela conta {account}."


# ---- Calendar ----


def _calendar_events_sync(account: str, days: int, timezone: str) -> str:
    calendar = _service("calendar", "v3", account)
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
    return "\n".join(lines)


def _create_event_sync(account: str, summary: str, start_iso: str, end_iso: str, timezone: str, description: str) -> str:
    calendar = _service("calendar", "v3", account)
    event = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_iso, "timeZone": timezone},
        "end": {"dateTime": end_iso, "timeZone": timezone},
    }
    created = calendar.events().insert(calendarId="primary", body=event).execute()
    return f"Compromisso criado na conta {account}: {summary} ({start_iso}). Link: {created.get('htmlLink', '')}"


# ---- agregação multi-contas ----


async def _for_each_account(account: str, worker) -> str:
    names = _pick(account)
    if isinstance(names, str):
        return names
    sections = []
    for name in names:
        try:
            out = await asyncio.to_thread(worker, name)
        except Exception as exc:
            out = f"erro nesta conta: {type(exc).__name__}: {exc}"
        sections.append(f"[conta {name}]\n{out}" if len(names) > 1 else out)
    return truncate("\n\n".join(sections))


NEED_ONE = "Há mais de uma conta Google conectada ({}). Diga em qual delas devo agir."


async def _list_emails(ctx: ToolContext, query: str = "is:unread category:primary", max_results: int = 10, account: str = "") -> str:
    return await _for_each_account(account, lambda n: _list_emails_sync(n, query, int(max_results)))


async def _read_email(ctx: ToolContext, message_id: str, account: str = "") -> str:
    name = _pick_one(account)
    if not name:
        return NEED_ONE.format(", ".join(accounts()))
    return await asyncio.to_thread(_read_email_sync, name, message_id)


async def _calendar_events(ctx: ToolContext, days: int = 1, account: str = "") -> str:
    return await _for_each_account(account, lambda n: _calendar_events_sync(n, int(days), ctx.config.timezone))


async def _create_event(ctx: ToolContext, summary: str, start_iso: str, end_iso: str, description: str = "", account: str = "") -> str:
    name = _pick_one(account)
    if not name:
        return NEED_ONE.format(", ".join(accounts()))
    return await asyncio.to_thread(
        _create_event_sync, name, summary, start_iso, end_iso, ctx.config.timezone, description
    )


async def _send_email(ctx: ToolContext, to: str, subject: str, body: str, account: str = "") -> str:
    name = _pick_one(account)
    if not name:
        return NEED_ONE.format(", ".join(accounts()))
    preview = body if len(body) <= 800 else body[:800] + "…"
    ok = await ctx.confirm(
        f"Enviar email pela conta Google '{name}'?\n\n"
        f"Para: {to}\nAssunto: {subject}\n\n{preview}"
    )
    if not ok:
        return "Envio cancelado pelo chefe."
    return await asyncio.to_thread(_send_email_sync, name, to, subject, body)


_ACCOUNT_PARAM = {
    "type": "STRING",
    "description": "Nome da conta Google (opcional; se omitido, todas nas consultas).",
}

LIST_EMAILS_TOOL = Tool(
    declaration={
        "name": "list_emails",
        "description": (
            "Lista emails do Gmail (todas as contas Google conectadas, ou uma específica). "
            "Por padrão, os não lidos da caixa principal. Aceita sintaxe de busca do Gmail "
            "(ex.: 'from:fulano', 'newer_than:2d')."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Busca Gmail. Padrão: is:unread category:primary"},
                "max_results": {"type": "INTEGER", "description": "Máximo de emails por conta (padrão 10)."},
                "account": _ACCOUNT_PARAM,
            },
        },
    },
    handler=_list_emails,
)

READ_EMAIL_TOOL = Tool(
    declaration={
        "name": "read_email",
        "description": "Lê o conteúdo completo de um email do Gmail pelo id retornado por list_emails.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message_id": {"type": "STRING", "description": "Id do email."},
                "account": {"type": "STRING", "description": "Conta Google dona do email (obrigatória se houver várias)."},
            },
            "required": ["message_id"],
        },
    },
    handler=_read_email,
)

CALENDAR_TOOL = Tool(
    declaration={
        "name": "calendar_events",
        "description": "Lista os compromissos do Google Calendar dos próximos dias (todas as contas, ou uma).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {"type": "INTEGER", "description": "Quantos dias à frente olhar (padrão 1 = hoje)."},
                "account": _ACCOUNT_PARAM,
            },
        },
    },
    handler=_calendar_events,
)

SEND_EMAIL_TOOL = Tool(
    declaration={
        "name": "send_email",
        "description": (
            "Envia um email pelo Gmail do chefe. O sistema SEMPRE pede confirmação "
            "dele antes de enviar — escreva o email completo e chame a ferramenta."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "to": {"type": "STRING", "description": "Destinatário (email)."},
                "subject": {"type": "STRING", "description": "Assunto."},
                "body": {"type": "STRING", "description": "Corpo do email, texto simples."},
                "account": {"type": "STRING", "description": "Conta Google remetente (obrigatória se houver várias)."},
            },
            "required": ["to", "subject", "body"],
        },
    },
    handler=_send_email,
)

CREATE_EVENT_TOOL = Tool(
    declaration={
        "name": "create_calendar_event",
        "description": (
            "Cria um compromisso no Google Calendar do chefe. Calcule horários absolutos "
            "a partir do horário atual do contexto."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "summary": {"type": "STRING", "description": "Título do compromisso."},
                "start_iso": {"type": "STRING", "description": "Início, ISO: AAAA-MM-DDTHH:MM:SS"},
                "end_iso": {"type": "STRING", "description": "Fim, ISO: AAAA-MM-DDTHH:MM:SS"},
                "description": {"type": "STRING", "description": "Detalhes (opcional)."},
                "account": {"type": "STRING", "description": "Conta Google onde criar (obrigatória se houver várias)."},
            },
            "required": ["summary", "start_iso", "end_iso"],
        },
    },
    handler=_create_event,
)
