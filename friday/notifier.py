"""Entrega educada de avisos proativos: horário de silêncio com fila persistente.

Avisos não-críticos fora do horário permitido ficam guardados em
state/avisos_pendentes.json e são entregues juntos na primeira hora boa.
Conversa direta com o chefe nunca passa por aqui.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .config import ROOT, Config

log = logging.getLogger("friday.notifier")

QUEUE_FILE = ROOT / "state" / "avisos_pendentes.json"


def _parse(hhmm: str) -> time:
    hours, minutes = hhmm.split(":")
    return time(int(hours), int(minutes))


class Notifier:
    def __init__(self, config: Config, send_raw, call_user=None):
        self.config = config
        self.send_raw = send_raw
        self.call_user = call_user      # async (texto) -> None, ligação telefônica
        self._servicos_push: list[str] = []
        self.start_quiet = _parse(config.quiet_start) if config.quiet_start else None
        self.end_quiet = _parse(config.quiet_end) if config.quiet_end else None

    def is_quiet(self, now: datetime | None = None) -> bool:
        if not self.start_quiet or not self.end_quiet:
            return False
        now = now or datetime.now(ZoneInfo(self.config.timezone))
        t = now.time()
        if self.start_quiet <= self.end_quiet:  # janela no mesmo dia
            return self.start_quiet <= t < self.end_quiet
        return t >= self.start_quiet or t < self.end_quiet  # cruza a meia-noite

    async def _push_celular(self, text: str, critico: bool):
        """Notificação no app do Home Assistant — a crítica fura o Não Perturbe."""
        import httpx

        if not self.config.ha_token:
            return
        async with httpx.AsyncClient(timeout=15) as client:
            cabecalho = {"Authorization": f"Bearer {self.config.ha_token}"}
            if not self._servicos_push:
                resp = await client.get(f"{self.config.ha_url}/api/services", headers=cabecalho)
                resp.raise_for_status()
                for dominio in resp.json():
                    if dominio.get("domain") == "notify":
                        self._servicos_push = [
                            s for s in dominio.get("services", {}) if s.startswith("mobile_app_")
                        ]
            for servico in self._servicos_push:
                dados = {"message": text[:900], "title": "JARVIS"}
                if critico:
                    # canal de alarme no Android / alerta crítico no iOS
                    dados["data"] = {
                        "ttl": 0, "priority": "high",
                        "channel": "alarm_stream",
                        "push": {"sound": {"name": "default", "critical": 1, "volume": 1.0}},
                    }
                await client.post(
                    f"{self.config.ha_url}/api/services/notify/{servico}",
                    headers=cabecalho, json=dados,
                )

    async def send(self, text: str, urgency: str = "normal", critical: bool = False):
        """Escada de urgência:
        normal   -> Telegram (respeita o horário de silêncio)
        high     -> Telegram + notificação no celular
        critical -> + notificação crítica, que fura o Não Perturbe
        decision -> + ligação telefônica (precisa de decisão do chefe)
        """
        if critical and urgency == "normal":   # compatibilidade
            urgency = "critical"

        if urgency == "decision" and self.call_user:
            try:
                await self.call_user(text)
                return
            except Exception:
                log.exception("ligação falhou — caindo para notificação crítica")
                urgency = "critical"

        if urgency in ("high", "critical"):
            try:
                await self._push_celular(text, critico=urgency == "critical")
            except Exception:
                log.exception("falha ao mandar push pelo Home Assistant")

        if urgency != "normal" or not self.is_quiet():
            await self.send_raw(text)
            return
        queue = self._load()
        stamp = datetime.now(ZoneInfo(self.config.timezone)).strftime("%H:%M")
        queue.append(f"[{stamp}] {text}")
        self._save(queue)
        log.info("aviso guardado para depois do silêncio (%d na fila)", len(queue))

    async def flush_if_allowed(self):
        """Job periódico: entrega a fila quando o silêncio acaba."""
        if self.is_quiet():
            return
        queue = self._load()
        if not queue:
            return
        self._save([])
        header = "🌅 Enquanto você descansava, guardei estes avisos:\n\n"
        await self.send_raw(header + "\n\n".join(queue))

    def attach(self, scheduler):
        scheduler.add_job(self.flush_if_allowed, "interval", minutes=5, id="notifier-flush")

    def _load(self) -> list[str]:
        if QUEUE_FILE.exists():
            try:
                return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return []
        return []

    def _save(self, queue: list[str]):
        QUEUE_FILE.parent.mkdir(exist_ok=True)
        QUEUE_FILE.write_text(json.dumps(queue, ensure_ascii=False, indent=1), encoding="utf-8")
