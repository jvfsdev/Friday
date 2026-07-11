"""Vigia de eventos: checagens baratas (sem IA) em intervalos; o cérebro só é
acionado quando um monitor muda de estado — economiza a cota do Gemini."""

from __future__ import annotations

import asyncio
import logging
import shutil

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .brain import Brain
from .config import Config, MonitorSpec

log = logging.getLogger("friday.monitor")


async def _run_quiet(command: str, timeout: int = 30) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_shell(
        command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, f"(comando excedeu {timeout}s)"
    return proc.returncode or 0, out.decode(errors="replace").strip()[-500:]


# Cada checagem retorna (problema?, detalhe legível).


async def _check_disk(params: dict) -> tuple[bool, str]:
    path = params.get("path", "/")
    threshold = float(params.get("threshold_percent", 90))
    usage = shutil.disk_usage(path)
    percent = usage.used / usage.total * 100
    free_gb = usage.free / 1e9
    return percent >= threshold, f"disco em {path}: {percent:.0f}% usado ({free_gb:.1f} GB livres)"


async def _check_command(params: dict) -> tuple[bool, str]:
    command = params["command"]
    code, out = await _run_quiet(command)
    detail = f"`{command}` → código {code}" + (f": {out}" if out else "")
    return code != 0, detail


async def _check_ping(params: dict) -> tuple[bool, str]:
    host = params["host"]
    code, _ = await _run_quiet(f"ping -c 1 {host}", timeout=6)
    online = code == 0
    alert_when = params.get("alert_when", "offline")
    problem = (not online) if alert_when == "offline" else online
    return problem, f"{host} está {'online' if online else 'offline'}"


CHECKS = {"disk": _check_disk, "command": _check_command, "ping": _check_ping}


class FridayMonitor:
    def __init__(self, config: Config, brain: Brain, send, scheduler: AsyncIOScheduler):
        self.config = config
        self.brain = brain
        self.send = send
        self.scheduler = scheduler
        self._state: dict[str, bool] = {}

    def start(self):
        for spec in self.config.monitors:
            if spec.check not in CHECKS:
                log.warning("monitor '%s': tipo desconhecido '%s' — ignorado", spec.name, spec.check)
                continue
            self.scheduler.add_job(
                self._run,
                "interval",
                minutes=spec.interval_minutes,
                args=[spec],
                id=f"monitor-{spec.name}",
            )
            log.info("monitor '%s' ativo (%s, a cada %s min)", spec.name, spec.check, spec.interval_minutes)

    async def _run(self, spec: MonitorSpec):
        try:
            problem, detail = await CHECKS[spec.check](spec.params)
        except Exception as exc:
            log.exception("monitor '%s' falhou", spec.name)
            problem, detail = True, f"a própria checagem falhou: {type(exc).__name__}: {exc}"

        previous = self._state.get(spec.name)
        self._state[spec.name] = problem
        try:
            if problem and previous is not True:
                # Só aqui a IA entra: avaliar o alerta e avisar com contexto.
                answer = await self.brain.ask(
                    f"[Alerta do monitor '{spec.name}' — detectado agora, avise o chefe "
                    f"de forma útil e sugira o que fazer] {detail}"
                )
                await self.send(f"🚨 {answer}")
            elif not problem and previous is True:
                await self.send(f"✅ Monitor '{spec.name}': normalizado ({detail}).")
        except Exception:
            log.exception("falha ao avisar sobre o monitor '%s'", spec.name)
