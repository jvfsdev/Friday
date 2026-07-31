"""Carrega .env + config.yaml e expõe a configuração da Friday."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT / "memory"
PERSONA_FILE = ROOT / "JARVIS.md"


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
    only_if: dict | None = None   # {entity, state} no Home Assistant


@dataclass
class MonitorSpec:
    name: str
    check: str            # disk | command | ping
    interval_minutes: int
    params: dict


@dataclass
class Config:
    gemini_api_key: str                       # primeira chave (compatibilidade)
    gemini_api_keys: list[str]                # todas as chaves, em ordem
    models: list[str]                         # escada de modelos, em ordem
    telegram_bot_token: str
    telegram_user_id: int | None
    ha_url: str
    ha_token: str
    notion_token: str
    ms_client_id: str
    model: str = "gemini-2.5-flash"
    fallback_model: str = "gemini-2.5-flash-lite"
    timezone: str = "America/Sao_Paulo"
    history_max_turns: int = 40
    quiet_start: str = ""              # ex.: "23:00" — vazio desativa
    quiet_end: str = ""                # ex.: "07:00"
    backup_dir: str = ""               # destino dos backups (vazio desativa)
    voice_enabled: bool = False        # voz na sala (mic/alto-falante locais)
    voice_settings: dict = field(default_factory=dict)
    mcp_servers: dict = field(default_factory=dict)  # servidores MCP plugáveis
    machines: dict[str, Machine] = field(default_factory=dict)
    routines: list[Routine] = field(default_factory=list)
    monitors: list[MonitorSpec] = field(default_factory=list)


def load_config() -> Config:
    load_dotenv(ROOT / ".env")

    raw: dict = {}
    # config.yaml é local (não versionado); o repositório traz o modelo.
    config_file = ROOT / "config.yaml"
    if not config_file.exists():
        config_file = ROOT / "config.example.yaml"
    if config_file.exists():
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}

    machines = {
        name: Machine(name=name, **spec)
        for name, spec in (raw.get("machines") or {}).items()
    }
    routines = [
        Routine(
            name=name,
            cron=spec["cron"],
            prompt=spec["prompt"].strip(),
            only_if=spec.get("only_if"),
        )
        for name, spec in (raw.get("routines") or {}).items()
    ]
    monitors = [
        MonitorSpec(
            name=name,
            check=spec["check"],
            interval_minutes=int(spec.get("interval_minutes", 15)),
            params={k: v for k, v in spec.items() if k not in ("check", "interval_minutes")},
        )
        for name, spec in (raw.get("monitors") or {}).items()
    ]

    user_id = os.getenv("TELEGRAM_USER_ID", "").strip()

    # Chaves: aceita lista separada por vírgula tanto em GEMINI_API_KEYS
    # quanto na GEMINI_API_KEY clássica (a primeira tem precedência).
    def _split_keys(value: str) -> list[str]:
        return [k.strip() for k in value.split(",") if k.strip()]

    keys = _split_keys(os.getenv("GEMINI_API_KEYS", "")) or _split_keys(
        os.getenv("GEMINI_API_KEY", "")
    )

    # Escada de modelos: lista `models:` ou os campos antigos model/fallback_model.
    models = raw.get("models") or [
        raw.get("model", "gemini-2.5-flash"),
        raw.get("fallback_model", "gemini-2.5-flash-lite"),
    ]
    models = list(dict.fromkeys(m for m in models if m))  # dedupe mantendo a ordem

    return Config(
        gemini_api_key=keys[0] if keys else "",
        gemini_api_keys=keys,
        models=models,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_user_id=int(user_id) if user_id else None,
        ha_url=os.getenv("HA_URL", "http://localhost:8123").strip().rstrip("/"),
        ha_token=os.getenv("HA_TOKEN", "").strip(),
        notion_token=os.getenv("NOTION_TOKEN", "").strip(),
        ms_client_id=os.getenv("MS_CLIENT_ID", "").strip(),
        model=raw.get("model", "gemini-2.5-flash"),
        fallback_model=raw.get("fallback_model", "gemini-2.5-flash-lite"),
        timezone=raw.get("timezone", "America/Sao_Paulo"),
        history_max_turns=int(raw.get("history_max_turns", 40)),
        machines=machines,
        routines=routines,
        monitors=monitors,
        quiet_start=str((raw.get("quiet_hours") or {}).get("inicio", "")),
        quiet_end=str((raw.get("quiet_hours") or {}).get("fim", "")),
        backup_dir=str(raw.get("backup_dir", "") or ""),
        voice_enabled=bool((raw.get("voice") or {}).get("enabled", False)),
        voice_settings=raw.get("voice") or {},
        mcp_servers=raw.get("mcp_servers") or {},
    )
