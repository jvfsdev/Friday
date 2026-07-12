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
    def __init__(self, config: Config, send_raw):
        self.config = config
        self.send_raw = send_raw
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

    async def send(self, text: str, critical: bool = False):
        if critical or not self.is_quiet():
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
