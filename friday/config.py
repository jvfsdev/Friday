"""Carrega .env + config.yaml e expõe a configuração da Friday."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT / "memory"
PERSONA_FILE = ROOT / "FRIDAY.md"


@dataclass
class Machine:
    name: str
    host: str
    user: str
    mac_address: str | None = None


@dataclass
class Routine:
    name: str
    cron: str
    prompt: str


@dataclass
class Config:
    gemini_api_key: str
    telegram_bot_token: str
    telegram_user_id: int | None
    ha_url: str
    ha_token: str
    notion_token: str
    model: str = "gemini-2.5-flash"
    timezone: str = "America/Sao_Paulo"
    history_max_turns: int = 40
    machines: dict[str, Machine] = field(default_factory=dict)
    routines: list[Routine] = field(default_factory=list)


def load_config() -> Config:
    load_dotenv(ROOT / ".env")

    raw: dict = {}
    config_file = ROOT / "config.yaml"
    if config_file.exists():
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}

    machines = {
        name: Machine(name=name, **spec)
        for name, spec in (raw.get("machines") or {}).items()
    }
    routines = [
        Routine(name=name, cron=spec["cron"], prompt=spec["prompt"].strip())
        for name, spec in (raw.get("routines") or {}).items()
    ]

    user_id = os.getenv("TELEGRAM_USER_ID", "").strip()
    return Config(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_user_id=int(user_id) if user_id else None,
        ha_url=os.getenv("HA_URL", "http://localhost:8123").strip().rstrip("/"),
        ha_token=os.getenv("HA_TOKEN", "").strip(),
        notion_token=os.getenv("NOTION_TOKEN", "").strip(),
        model=raw.get("model", "gemini-2.5-flash"),
        timezone=raw.get("timezone", "America/Sao_Paulo"),
        history_max_turns=int(raw.get("history_max_turns", 40)),
        machines=machines,
        routines=routines,
    )
