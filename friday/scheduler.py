"""Rotinas proativas: a Friday age sozinha em horários configurados."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .brain import Brain
from .config import Config
from .tools import Tool

log = logging.getLogger("friday.scheduler")


class FridayScheduler:
    def __init__(self, config: Config, brain: Brain, send: Callable[[str], Awaitable[None]]):
        self.config = config
        self.brain = brain
        self.send = send  # entrega mensagens proativas (Telegram ou console)
        self.scheduler = AsyncIOScheduler(timezone=config.timezone)

    def start(self):
        for routine in self.config.routines:
            self.scheduler.add_job(
                self._run_routine,
                CronTrigger.from_crontab(routine.cron, timezone=self.config.timezone),
                args=[routine.name, routine.prompt],
                id=routine.name,
            )
            log.info("rotina '%s' agendada (%s)", routine.name, routine.cron)
        self.scheduler.start()

    async def _run_routine(self, name: str, prompt: str):
        log.info("disparando rotina '%s'", name)
        try:
            answer = await self.brain.ask(
                f"[Rotina automática '{name}' disparou agora — execute e responda ao chefe] {prompt}"
            )
            await self.send(answer)
        except Exception as exc:
            log.exception("rotina '%s' falhou", name)
            # Watchdog: falha silenciosa não existe — o chefe fica sabendo.
            try:
                await self.send(f"⚠️ A rotina '{name}' falhou: {type(exc).__name__}: {exc}")
            except Exception:
                log.exception("nem o aviso de falha da rotina '%s' saiu", name)

    def add_reminder(self, when: datetime, message: str) -> None:
        self.scheduler.add_job(
            self._run_routine,
            DateTrigger(run_date=when, timezone=self.config.timezone),
            args=["lembrete", f"Chegou a hora deste lembrete, avise o chefe: {message}"],
        )

    def reminder_tool(self) -> Tool:
        """Ferramenta para a própria Friday criar lembretes dinâmicos."""

        async def _handler(ctx, datetime_iso: str, message: str) -> str:
            try:
                when = datetime.fromisoformat(datetime_iso)
            except ValueError:
                return "Data inválida. Use o formato ISO: AAAA-MM-DDTHH:MM."
            self.add_reminder(when, message)
            return f"Lembrete agendado para {when.strftime('%d/%m/%Y %H:%M')}."

        return Tool(
            declaration={
                "name": "schedule_reminder",
                "description": (
                    "Agenda um lembrete único para o futuro. Na hora marcada a Friday "
                    "avisa o chefe. Calcule o horário absoluto a partir do horário atual "
                    "do contexto (ex.: 'daqui 20 min' → agora + 20 min)."
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "datetime_iso": {
                            "type": "STRING",
                            "description": "Data/hora no formato ISO: AAAA-MM-DDTHH:MM",
                        },
                        "message": {"type": "STRING", "description": "O que lembrar."},
                    },
                    "required": ["datetime_iso", "message"],
                },
            },
            handler=_handler,
        )
