"""Emails de contas Microsoft (@hotmail, @outlook, @live) via Microsoft Graph.

A Microsoft aposentou o acesso por senha/IMAP básico em 2024, então o caminho
é a API Graph com OAuth. Autorização: `python scripts/microsoft_auth.py [nome]`
(device code — funciona até no servidor sem navegador). Requer MS_CLIENT_ID
no .env — veja DEPLOY.md.
"""

from __future__ import annotations

import asyncio

import httpx

from . import Tool, ToolContext, truncate
from ..config import ROOT

STATE_DIR = ROOT / "state"
GRAPH = "https://graph.microsoft.com/v1.0"
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["Mail.Read"]


def cache_file(account: str):
    return STATE_DIR / f"ms_token_{account}.json"


def accounts() -> list[str]:
    return sorted(p.stem.removeprefix("ms_token_") for p in STATE_DIR.glob("ms_token_*.json"))


def has_credentials() -> bool:
    return bool(accounts())


def _access_token_sync(client_id: str, account: str) -> str:
    import msal

    cache = msal.SerializableTokenCache()
    path = cache_file(account)
    cache.deserialize(path.read_text(encoding="utf-8"))
    app = msal.PublicClientApplication(client_id, authority=AUTHORITY, token_cache=cache)
    known = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=known[0]) if known else None
    if cache.has_state_changed:
        path.write_text(cache.serialize(), encoding="utf-8")
    if not result or "access_token" not in result:
        raise RuntimeError(
            f"login da conta Microsoft '{account}' expirou — rode scripts/microsoft_auth.py {account} de novo"
        )
    return result["access_token"]


async def _graph_get(ctx: ToolContext, account: str, path: str, params: dict) -> dict:
    token = await asyncio.to_thread(_access_token_sync, ctx.config.ms_client_id, account)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{GRAPH}{path}", params=params, headers={"Authorization": f"Bearer {token}"}
        )
    resp.raise_for_status()
    return resp.json()


def _pick(account: str) -> list[str] | str:
    available = accounts()
    if account:
        if account in available:
            return [account]
        return f"Conta Microsoft '{account}' não existe. Contas conectadas: {', '.join(available)}."
    return available


async def _list_sync_one(ctx: ToolContext, account: str, only_unread: bool, max_results: int) -> str:
    params = {
        "$top": max_results,
        "$select": "id,from,subject,bodyPreview,receivedDateTime",
    }
    if only_unread:
        params["$filter"] = "isRead eq false"
    data = await _graph_get(ctx, account, "/me/mailFolders/inbox/messages", params)
    messages = data.get("value", [])
    if not messages:
        return "Nenhum email " + ("não lido." if only_unread else "encontrado.")
    lines = []
    for msg in messages:
        sender = msg.get("from", {}).get("emailAddress", {})
        lines.append(
            f"id: {msg['id']}\n"
            f"  de: {sender.get('name', '?')} <{sender.get('address', '?')}>\n"
            f"  assunto: {msg.get('subject') or '(sem assunto)'}\n"
            f"  resumo: {(msg.get('bodyPreview') or '')[:150]}"
        )
    return "\n".join(lines)


async def _list_emails(ctx: ToolContext, only_unread: bool = True, max_results: int = 10, account: str = "") -> str:
    names = _pick(account)
    if isinstance(names, str):
        return names
    sections = []
    for name in names:
        try:
            out = await _list_sync_one(ctx, name, bool(only_unread), int(max_results))
        except Exception as exc:
            out = f"erro nesta conta: {type(exc).__name__}: {exc}"
        sections.append(f"[conta {name}]\n{out}" if len(names) > 1 else out)
    return truncate("\n\n".join(sections))


async def _read_email(ctx: ToolContext, message_id: str, account: str = "") -> str:
    available = accounts()
    name = account or (available[0] if len(available) == 1 else "")
    if not name:
        return f"Há mais de uma conta Microsoft ({', '.join(available)}). Diga qual delas."
    if name not in available:
        return f"Conta Microsoft '{name}' não existe. Contas: {', '.join(available)}."
    msg = await _graph_get(
        ctx, name, f"/me/messages/{message_id}",
        {"$select": "subject,from,receivedDateTime,body"},
    )
    sender = msg.get("from", {}).get("emailAddress", {})
    body = msg.get("body", {})
    text = body.get("content", "")
    if body.get("contentType") == "html":
        from .web import _TextExtractor

        parser = _TextExtractor()
        parser.feed(text)
        text = "\n".join(parser.chunks)
    return truncate(
        f"De: {sender.get('name', '?')} <{sender.get('address', '?')}>\n"
        f"Assunto: {msg.get('subject') or '(sem assunto)'}\n"
        f"Data: {msg.get('receivedDateTime', '?')}\n\n{text}"
    )


LIST_TOOL = Tool(
    declaration={
        "name": "outlook_emails",
        "description": (
            "Lista emails das contas Microsoft do chefe (@hotmail/@outlook/@live) — "
            "todas as contas conectadas, ou uma específica. Por padrão, só os não lidos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "only_unread": {"type": "BOOLEAN", "description": "Só não lidos (padrão true)."},
                "max_results": {"type": "INTEGER", "description": "Máximo por conta (padrão 10)."},
                "account": {"type": "STRING", "description": "Nome da conta Microsoft (opcional)."},
            },
        },
    },
    handler=_list_emails,
)

READ_TOOL = Tool(
    declaration={
        "name": "outlook_read_email",
        "description": "Lê o conteúdo completo de um email Microsoft pelo id retornado por outlook_emails.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message_id": {"type": "STRING", "description": "Id do email."},
                "account": {"type": "STRING", "description": "Conta Microsoft dona do email (obrigatória se houver várias)."},
            },
            "required": ["message_id"],
        },
    },
    handler=_read_email,
)
