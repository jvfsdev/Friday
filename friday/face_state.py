"""Barramento de estado do rosto: o processo do JARVIS escreve, o rosto lê.

Arquivo JSON simples (state/face_state.json) — robusto, sem dependências,
e cada processo pode reiniciar sem derrubar o outro. Só há escrita quando
algo muda, então o desgaste é desprezível.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import ROOT

STATE_FILE = ROOT / "state" / "face_state.json"

_last: dict = {}


def _write(data: dict):
    global _last
    if data == _last:
        return
    _last = data
    STATE_FILE.parent.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def publish(state: str, caption: str | None = None):
    """Atualiza o estado do orbe (idle/listening/thinking/speaking)."""
    data = read()
    data["state"] = state
    if caption is not None:
        data["caption"] = caption
    data["updated_at"] = time.time()
    _write(data)


def set_hud(key: str, value: str):
    """Atualiza um canto informativo (clima, agenda, monitores...)."""
    data = read()
    data.setdefault("hud", {})[key] = value
    data["updated_at"] = time.time()
    _write(data)


def read() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "idle", "caption": "", "hud": {}}
