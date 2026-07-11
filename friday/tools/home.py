"""Casa inteligente via Home Assistant (REST API)."""

from __future__ import annotations

import httpx

from . import Tool, ToolContext, truncate

USEFUL_DOMAINS = ("light", "switch", "climate", "fan", "media_player", "cover", "lock", "scene")


def _client(ctx: ToolContext) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{ctx.config.ha_url}/api",
        headers={"Authorization": f"Bearer {ctx.config.ha_token}"},
        timeout=20,
    )


async def _devices(ctx: ToolContext) -> str:
    async with _client(ctx) as client:
        resp = await client.get("/states")
        resp.raise_for_status()
    lines = []
    for state in resp.json():
        entity_id = state["entity_id"]
        if entity_id.split(".")[0] in USEFUL_DOMAINS:
            name = state.get("attributes", {}).get("friendly_name", entity_id)
            lines.append(f"{entity_id} — {name} — estado: {state['state']}")
    return truncate("\n".join(lines)) if lines else "Nenhum dispositivo encontrado no Home Assistant."


async def _control(ctx: ToolContext, entity_id: str, action: str) -> str:
    domain = entity_id.split(".")[0]
    if action not in ("turn_on", "turn_off", "toggle"):
        return "Ação inválida. Use turn_on, turn_off ou toggle."
    service_domain = "homeassistant" if domain == "scene" and action != "turn_on" else domain
    async with _client(ctx) as client:
        resp = await client.post(f"/services/{service_domain}/{action}", json={"entity_id": entity_id})
        if resp.status_code >= 400:
            return f"Home Assistant recusou ({resp.status_code}): {resp.text[:300]}"
    return f"Feito: {action} em {entity_id}."


async def _announce(ctx: ToolContext, message: str) -> str:
    # Requer a integração Alexa Media Player no Home Assistant (serviço notify.alexa_media).
    async with _client(ctx) as client:
        resp = await client.post(
            "/services/notify/alexa_media",
            json={"message": message, "data": {"type": "announce"}},
        )
        if resp.status_code >= 400:
            return (
                f"Não consegui anunciar ({resp.status_code}). "
                "A integração Alexa Media Player está instalada no Home Assistant?"
            )
    return "Anunciado nos alto-falantes."


DEVICES_TOOL = Tool(
    declaration={
        "name": "home_devices",
        "description": "Lista os dispositivos da casa (luzes, tomadas, cenas...) e seus estados.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    handler=_devices,
)

CONTROL_TOOL = Tool(
    declaration={
        "name": "home_control",
        "description": "Liga, desliga ou alterna um dispositivo da casa pelo entity_id.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "entity_id": {"type": "STRING", "description": "Ex.: light.sala, switch.cafeteira"},
                "action": {"type": "STRING", "description": "turn_on, turn_off ou toggle"},
            },
            "required": ["entity_id", "action"],
        },
    },
    handler=_control,
)

ANNOUNCE_TOOL = Tool(
    declaration={
        "name": "home_announce",
        "description": "Faz um anúncio de voz nos alto-falantes Echo (Alexa) da casa.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message": {"type": "STRING", "description": "O que anunciar, em português."}
            },
            "required": ["message"],
        },
    },
    handler=_announce,
)
