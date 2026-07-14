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


async def _ha_get_state(config: Config, entity: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{config.ha_url}/api/states/{entity}",
            headers={"Authorization": f"Bearer {config.ha_token}"},
        )
        resp.raise_for_status()
        return resp.json().get("state", "unknown")


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


class _HaStateCheck:
    """Dispara quando uma entidade do Home Assistant entra no estado configurado.

    params: entity, to (estado-alvo), from (opcional: só dispara vindo DESSE
    estado — evita disparo no boot), only_if: {entity, state} opcional.
    Ex.: person.joao → to: home, from: not_home ("chegou em casa");
    binary_sensor.porta → on com only_if person.joao = not_home.
    """

    def __init__(self, config: Config):
        self.config = config
        self.last: str | None = None

    async def __call__(self, params: dict) -> tuple[bool, str]:
        entity = params["entity"]
        target = str(params["to"])
        state = await _ha_get_state(self.config, entity)
        previous, self.last = self.last, state
        hit = state == target
        detail = f"{entity}: '{previous}' → '{state}'"
        if hit and "from" in params:
            # Transição explícita: precisa ter VISTO o estado de origem antes.
            if previous != str(params["from"]):
                return False, detail + " (sem a transição de origem exigida)"
        cond = params.get("only_if")
        if hit and cond:
            cond_state = await _ha_get_state(self.config, cond["entity"])
            if cond_state != str(cond["state"]):
                return False, detail + f" (ignorado: {cond['entity']}='{cond_state}')"
            detail += f" e {cond['entity']}='{cond_state}'"
        return hit, detail


class FridayMonitor:
    def __init__(self, config: Config, brain: Brain, send, scheduler: AsyncIOScheduler):
        self.config = config
        self.brain = brain
        self.send = send
        self.scheduler = scheduler
        self._state: dict[str, bool] = {}
        self._checks: dict[str, object] = {}  # instâncias com memória, por monitor

    def _check_for(self, spec: MonitorSpec):
        if spec.check == "ha_state":
            if not self.config.ha_token:
                return None
            if spec.name not in self._checks:
                self._checks[spec.name] = _HaStateCheck(self.config)
            return self._checks[spec.name]
        return CHECKS.get(spec.check)

    def start(self):
        for spec in self.config.monitors:
            if self._check_for(spec) is None:
                log.warning("monitor '%s': tipo '%s' desconhecido ou sem HA configurado — ignorado",
                            spec.name, spec.check)
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
            problem, detail = await self._check_for(spec)(spec.params)
        except Exception as exc:
            log.exception("monitor '%s' falhou", spec.name)
            problem, detail = True, f"a própria checagem falhou: {type(exc).__name__}: {exc}"

        critical = bool(spec.params.get("critical"))
        previous = self._state.get(spec.name)
        self._state[spec.name] = problem
        self._update_face()
        try:
            if problem and previous is not True:
                # Só aqui a IA entra: avaliar o evento e avisar com contexto.
                icone = spec.params.get("icon", "🚨")
                instrucao = spec.params.get(
                    "prompt",
                    f"Alerta do monitor '{spec.name}' — detectado agora, avise o chefe "
                    "de forma útil e sugira o que fazer",
                )
                answer = await self.brain.ask(f"[{instrucao}] {detail}")
                await self.send(f"{icone} {answer}", critical=critical)
            elif not problem and previous is True and spec.params.get("notify_recovery", True):
                await self.send(f"✅ Monitor '{spec.name}': normalizado ({detail}).", critical=False)
        except Exception:
            log.exception("falha ao avisar sobre o monitor '%s'", spec.name)

    def _update_face(self):
        from . import face_state

        problemas = sorted(n for n, ruim in self._state.items() if ruim)
        texto = "monitores ok" if not problemas else "⚠ " + ", ".join(problemas)
        try:
            face_state.set_hud("monitores", texto)
        except Exception:
            pass
