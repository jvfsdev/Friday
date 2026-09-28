"""Alimenta a tela de repouso do rosto: clima e o que ainda falta na agenda.

O rosto (friday/face.py) só lê state/face_state.json; quem escreve é o
processo principal, que tem as credenciais. Roda a cada poucos minutos —
falhar aqui nunca pode derrubar nada, no máximo deixa o dado velho.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import face_state

log = logging.getLogger("friday.face_dados")

CONDICOES = {
    "sunny": "sol", "clear-night": "céu limpo", "partlycloudy": "parcialmente nublado",
    "cloudy": "nublado", "rainy": "chuva", "pouring": "chuva forte",
    "lightning": "raios", "lightning-rainy": "tempestade", "fog": "neblina",
    "windy": "vento", "windy-variant": "vento", "snowy": "neve", "hail": "granizo",
}


async def _clima(config) -> str:
    if not config.ha_token:
        return ""
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{config.ha_url}/api/states",
                                headers={"Authorization": f"Bearer {config.ha_token}"})
        resp.raise_for_status()
    tempo = next((e for e in resp.json() if e["entity_id"].startswith("weather.")), None)
    if not tempo:
        return ""
    temp = tempo["attributes"].get("temperature")
    cond = CONDICOES.get(tempo["state"], tempo["state"])
    return f"{round(temp)}° · {cond}" if temp is not None else cond


def _agenda_sync(config) -> str:
    from .tools import google_workspace as gw

    fuso = ZoneInfo(config.timezone)
    agora = datetime.now(fuso)
    fim = agora.replace(hour=23, minute=59, second=59)
    itens = []
    for conta in gw.accounts():
        try:
            eventos = gw._service("calendar", "v3", conta).events().list(
                calendarId="primary", timeMin=agora.isoformat(), timeMax=fim.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=5,
            ).execute().get("items", [])
        except Exception as exc:
            log.warning("agenda da conta %s indisponível para o rosto: %s", conta, exc)
            continue
        for ev in eventos:
            inicio = ev["start"].get("dateTime")
            titulo = (ev.get("summary") or "(sem título)").strip()
            if inicio:
                quando = datetime.fromisoformat(inicio).astimezone(fuso)
                itens.append((quando, f"{quando:%H:%M} {titulo}"))
            else:
                itens.append((agora - timedelta(seconds=1), f"Dia todo · {titulo}"))
    itens.sort(key=lambda i: i[0])
    return " | ".join(texto for _, texto in itens[:3])


async def atualizar(config):
    try:
        face_state.set_hud("clima", await _clima(config))
    except Exception as exc:
        log.warning("clima para o rosto: %s", exc)
    try:
        face_state.set_hud("agenda", await asyncio.to_thread(_agenda_sync, config))
    except Exception as exc:
        log.warning("agenda para o rosto: %s", exc)
