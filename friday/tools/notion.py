"""Notion via API oficial (token de integração interna)."""

from __future__ import annotations

import httpx

from . import Tool, ToolContext, truncate

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def _client(ctx: ToolContext) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=API,
        headers={
            "Authorization": f"Bearer {ctx.config.notion_token}",
            "Notion-Version": VERSION,
        },
        timeout=30,
    )


def _title_of(result: dict) -> str:
    if result["object"] == "database":
        parts = result.get("title", [])
    else:
        parts = next(
            (p.get("title", []) for p in result.get("properties", {}).values() if p.get("type") == "title"),
            [],
        )
    return "".join(t.get("plain_text", "") for t in parts) or "(sem título)"


async def _search(ctx: ToolContext, query: str) -> str:
    async with _client(ctx) as client:
        resp = await client.post("/search", json={"query": query, "page_size": 10})
    if resp.status_code >= 400:
        return f"Notion recusou ({resp.status_code}): {resp.text[:300]}"
    results = resp.json().get("results", [])
    if not results:
        return f"Nada encontrado no Notion para {query!r}. (A página foi compartilhada com a integração?)"
    lines = [f"{_title_of(r)} — {r['object']} — id: {r['id']}" for r in results]
    return truncate("\n".join(lines))


async def _read_page(ctx: ToolContext, page_id: str) -> str:
    async with _client(ctx) as client:
        resp = await client.get(f"/blocks/{page_id}/children", params={"page_size": 100})
    if resp.status_code >= 400:
        return f"Notion recusou ({resp.status_code}): {resp.text[:300]}"
    lines = []
    for block in resp.json().get("results", []):
        content = block.get(block.get("type"), {})
        text = "".join(t.get("plain_text", "") for t in content.get("rich_text", []))
        if text:
            prefix = "- " if "list_item" in block["type"] else ""
            lines.append(prefix + text)
    return truncate("\n".join(lines)) if lines else "(página sem texto ou blocos não suportados)"


async def _append_note(ctx: ToolContext, page_id: str, text: str) -> str:
    block = {
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
            }
        ]
    }
    async with _client(ctx) as client:
        resp = await client.patch(f"/blocks/{page_id}/children", json=block)
    if resp.status_code >= 400:
        return f"Notion recusou ({resp.status_code}): {resp.text[:300]}"
    return "Anotado na página do Notion."


SEARCH_TOOL = Tool(
    declaration={
        "name": "notion_search",
        "description": "Busca páginas e bancos de dados no Notion do chefe.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Termo de busca."}
            },
            "required": ["query"],
        },
    },
    handler=_search,
)

READ_PAGE_TOOL = Tool(
    declaration={
        "name": "notion_read_page",
        "description": "Lê o conteúdo de uma página do Notion pelo id retornado por notion_search.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "page_id": {"type": "STRING", "description": "Id da página."}
            },
            "required": ["page_id"],
        },
    },
    handler=_read_page,
)

APPEND_TOOL = Tool(
    declaration={
        "name": "notion_append_note",
        "description": "Acrescenta um parágrafo de texto ao final de uma página do Notion.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "page_id": {"type": "STRING", "description": "Id da página."},
                "text": {"type": "STRING", "description": "Texto a acrescentar."},
            },
            "required": ["page_id", "text"],
        },
    },
    handler=_append_note,
)
