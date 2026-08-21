"""Estados que o JARVIS liga e desliga sozinho, a pedido do chefe.

O primeiro é o não perturbe: "vou entrar numa reunião" desliga ligação e
alerta crítico e deixa só a mensagem no Telegram, sem o chefe precisar mexer
em configuração nenhuma.

Fica em arquivo (state/flags.json) porque precisa sobreviver a restart: um
não perturbe que evapora quando o serviço reinicia no meio da reunião não
serve para nada. Todo estado tem prazo — modo que alguém esquece ligado é
pior que modo nenhum.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import ROOT

log = logging.getLogger("friday.flags")

ARQUIVO = ROOT / "state" / "flags.json"


def _agora(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def _ler() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _gravar(dados: dict):
    ARQUIVO.parent.mkdir(exist_ok=True)
    tmp = ARQUIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ARQUIVO)


def ativar(nome: str, minutos: int | None, motivo: str, tz: str) -> str:
    """Liga um estado por um tempo. minutos=None deixa até desligarem."""
    dados = _ler()
    fim = None
    if minutos:
        fim = (_agora(tz) + timedelta(minutes=minutos)).isoformat(timespec="minutes")
    dados[nome] = {"ativo": True, "ate": fim, "motivo": motivo}
    _gravar(dados)
    log.info("estado '%s' ligado (até %s): %s", nome, fim or "eu desligar", motivo)
    return fim or ""


def desativar(nome: str) -> bool:
    dados = _ler()
    if nome not in dados:
        return False
    dados.pop(nome)
    _gravar(dados)
    log.info("estado '%s' desligado", nome)
    return True


def ativo(nome: str, tz: str = "America/Sao_Paulo") -> bool:
    """Estado vencido se apaga sozinho — ninguém precisa lembrar de desligar."""
    dados = _ler()
    estado = dados.get(nome)
    if not estado or not estado.get("ativo"):
        return False
    prazo = estado.get("ate")
    if prazo and _agora(tz).isoformat(timespec="minutes") >= prazo:
        desativar(nome)
        return False
    return True


def detalhe(nome: str, tz: str = "America/Sao_Paulo") -> dict | None:
    return _ler().get(nome) if ativo(nome, tz) else None


def listar(tz: str = "America/Sao_Paulo") -> dict:
    return {nome: _ler()[nome] for nome in list(_ler()) if ativo(nome, tz)}
