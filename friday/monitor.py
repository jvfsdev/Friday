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


class _DriveFolderCheck:
    """Dispara quando aparece arquivo novo numa pasta do Drive.

    params: folder_id, account (opcional), prompt (opcional).
    Os ids já vistos ficam em state/ — na primeira execução ele apenas
    memoriza o que já existe, para não despejar a pasta inteira no chefe.
    """

    def __init__(self, config: Config):
        self.config = config

    def _arquivo_estado(self, nome: str):
        from .config import ROOT

        return ROOT / "state" / f"drive_seen_{nome}.json"

    async def __call__(self, params: dict) -> tuple[bool, str]:
        import json

        from .tools.google_workspace import _pick_one, _service

        conta = _pick_one(params.get("account", ""))
        if not conta:
            return False, "monitor do Drive precisa de 'account' quando há várias contas"

        def listar():
            drive = _service("drive", "v3", conta)
            resp = drive.files().list(
                q=f"'{params['folder_id']}' in parents and trashed = false",
                fields="files(id,name,mimeType)",
                pageSize=50,
                orderBy="createdTime desc",
            ).execute()
            return resp.get("files", [])

        arquivos = await asyncio.to_thread(listar)
        caminho = self._arquivo_estado(params.get("_nome", "pasta"))
        try:
            vistos = set(json.loads(caminho.read_text(encoding="utf-8")))
            primeira_vez = False
        except (OSError, json.JSONDecodeError):
            vistos, primeira_vez = set(), True

        atuais = {f["id"] for f in arquivos}
        novos = [f for f in arquivos if f["id"] not in vistos]
        caminho.parent.mkdir(exist_ok=True)
        caminho.write_text(json.dumps(sorted(atuais)), encoding="utf-8")

        if primeira_vez or not novos:
            return False, f"{len(arquivos)} arquivo(s) na pasta, nenhum novo"
        descricao = "; ".join(f"{f['name']} (id: {f['id']}, conta {conta})" for f in novos[:5])
        return True, f"arquivo(s) novo(s) no Drive: {descricao}"


class _GmailCheck:
    """Dispara quando chega email novo que casa com a busca configurada.

    params: query (sintaxe do Gmail), account, prompt/pipeline opcionais.
    Na primeira execução só memoriza o que já existe.
    """

    def __init__(self, config: Config):
        self.config = config

    async def __call__(self, params: dict) -> tuple[bool, str]:
        import json

        from .config import ROOT
        from .tools.google_workspace import _pick_one, _service

        conta = _pick_one(params.get("account", ""))
        if not conta:
            return False, "monitor de email precisa de 'account' quando há várias contas"
        consulta = params.get("query", "is:unread category:primary")

        def buscar():
            gmail = _service("gmail", "v1", conta)
            resp = gmail.users().messages().list(
                userId="me", q=consulta, maxResults=10
            ).execute()
            achados = []
            for ref in resp.get("messages", []):
                msg = gmail.users().messages().get(
                    userId="me", id=ref["id"], format="metadata",
                    metadataHeaders=["From", "Subject"],
                ).execute()
                cab = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
                achados.append({
                    "id": ref["id"],
                    "de": cab.get("From", "?"),
                    "assunto": cab.get("Subject", "(sem assunto)"),
                    "resumo": msg.get("snippet", "")[:200],
                })
            return achados

        emails = await asyncio.to_thread(buscar)
        caminho = ROOT / "state" / f"gmail_seen_{params.get('_nome', 'monitor')}.json"
        try:
            vistos = set(json.loads(caminho.read_text(encoding="utf-8")))
            primeira_vez = False
        except (OSError, json.JSONDecodeError):
            vistos, primeira_vez = set(), True

        novos = [e for e in emails if e["id"] not in vistos]
        caminho.parent.mkdir(exist_ok=True)
        caminho.write_text(json.dumps(sorted({e["id"] for e in emails} | vistos)[-200:]), encoding="utf-8")

        if primeira_vez or not novos:
            return False, f"{len(emails)} email(s) na busca, nenhum novo"

        self.ultimos = novos  # o pipeline lê daqui
        descricao = "; ".join(f"{e['assunto']} (de {e['de']}, id {e['id']}, conta {conta})" for e in novos[:3])
        return True, f"email(s) novo(s): {descricao}"


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
    def __init__(self, config: Config, brain: Brain, send, scheduler: AsyncIOScheduler,
                 pipelines: dict | None = None):
        self.config = config
        self.brain = brain
        self.send = send
        self.scheduler = scheduler
        self.pipelines = pipelines or {}
        self._state: dict[str, bool] = {}
        self._checks: dict[str, object] = {}  # instâncias com memória, por monitor
        self.dinamicos: dict[str, MonitorSpec] = {}

    # ---- monitores que o próprio JARVIS cria ----

    @staticmethod
    def _arquivo_dinamicos():
        from .config import ROOT

        return ROOT / "state" / "monitores_dinamicos.json"

    def _salvar_dinamicos(self):
        import json

        caminho = self._arquivo_dinamicos()
        caminho.parent.mkdir(exist_ok=True)
        caminho.write_text(json.dumps(
            {n: {"check": s.check, "interval_minutes": s.interval_minutes, "params": s.params}
             for n, s in self.dinamicos.items()},
            ensure_ascii=False, indent=1), encoding="utf-8")

    def _carregar_dinamicos(self):
        import json

        try:
            dados = json.loads(self._arquivo_dinamicos().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for nome, spec in dados.items():
            try:
                self.adicionar(MonitorSpec(name=nome, check=spec["check"],
                                           interval_minutes=int(spec["interval_minutes"]),
                                           params=spec["params"]), persistir=False)
            except Exception:
                log.exception("monitor dinâmico '%s' não pôde ser restaurado", nome)

    def adicionar(self, spec: MonitorSpec, persistir: bool = True) -> str:
        """Agenda um monitor agora, sem reiniciar o serviço."""
        if self._check_for(spec) is None:
            return f"tipo de monitor '{spec.check}' não existe ou depende de algo não configurado"
        if spec.name in self.dinamicos or any(m.name == spec.name for m in self.config.monitors):
            return f"já existe um monitor chamado '{spec.name}'"
        self.scheduler.add_job(
            self._run, "interval", minutes=spec.interval_minutes,
            args=[spec], id=f"monitor-{spec.name}", replace_existing=True,
        )
        self.dinamicos[spec.name] = spec
        if persistir:
            self._salvar_dinamicos()
        log.info("monitor dinâmico '%s' criado (%s, a cada %s min)",
                 spec.name, spec.check, spec.interval_minutes)
        return ""

    def remover(self, nome: str) -> bool:
        if nome not in self.dinamicos:
            return False
        try:
            self.scheduler.remove_job(f"monitor-{nome}")
        except Exception:
            pass
        self.dinamicos.pop(nome, None)
        self._state.pop(nome, None)
        self._checks.pop(nome, None)
        self._salvar_dinamicos()
        log.info("monitor dinâmico '%s' removido", nome)
        return True

    def listar(self) -> str:
        fixos = [f"  {m.name} ({m.check}, a cada {m.interval_minutes} min) — do config"
                 for m in self.config.monitors]
        criados = []
        for nome, spec in self.dinamicos.items():
            extra = []
            if spec.params.get("uma_vez"):
                extra.append("dispara uma vez")
            if spec.params.get("expira_em"):
                extra.append(f"expira {spec.params['expira_em'][:16]}")
            criados.append(f"  {nome} ({spec.check}, a cada {spec.interval_minutes} min)"
                           + (f" — {', '.join(extra)}" if extra else ""))
        partes = []
        if fixos:
            partes.append("Fixos:\n" + "\n".join(fixos))
        if criados:
            partes.append("Criados a pedido:\n" + "\n".join(criados))
        return "\n\n".join(partes) or "Nenhum monitor ativo."

    def _check_for(self, spec: MonitorSpec):
        if spec.check == "drive_folder":
            spec.params.setdefault("_nome", spec.name)
            if spec.name not in self._checks:
                self._checks[spec.name] = _DriveFolderCheck(self.config)
            return self._checks[spec.name]
        if spec.check == "gmail":
            spec.params.setdefault("_nome", spec.name)
            if spec.name not in self._checks:
                self._checks[spec.name] = _GmailCheck(self.config)
            return self._checks[spec.name]
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
                replace_existing=True,   # start() duas vezes não pode explodir
            )
            log.info("monitor '%s' ativo (%s, a cada %s min)", spec.name, spec.check, spec.interval_minutes)
        self._carregar_dinamicos()

    async def _run(self, spec: MonitorSpec):
        try:
            problem, detail = await self._check_for(spec)(spec.params)
        except Exception as exc:
            log.exception("monitor '%s' falhou", spec.name)
            problem, detail = True, f"a própria checagem falhou: {type(exc).__name__}: {exc}"

        critical = bool(spec.params.get("critical"))
        urgencia = spec.params.get("urgency") or ("critical" if critical else "normal")

        # Vigia com prazo: "me avisa quando chegar o email do banco" não pode
        # ficar vigiando para sempre depois de cumprir o combinado.
        prazo = spec.params.get("expira_em")
        if prazo:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            agora = datetime.now(ZoneInfo(self.config.timezone)).isoformat(timespec="minutes")
            if agora >= prazo:
                if self.remover(spec.name):
                    await self.send(f"⏳ Monitor '{spec.name}' expirou e foi removido.")
                return
        previous = self._state.get(spec.name)
        self._state[spec.name] = problem
        self._update_face()
        try:
            if problem and previous is not True and spec.params.get("pipeline"):
                # Evento vai para um roteiro próprio (ex.: reclamação de cliente).
                pipeline = self.pipelines.get(spec.params["pipeline"])
                if pipeline:
                    await pipeline.processar(detail, origem=f"monitor {spec.name}")
                    return
                log.warning("pipeline '%s' não registrado", spec.params["pipeline"])

            if problem and previous is not True:
                # Só aqui a IA entra: avaliar o evento e avisar com contexto.
                icone = spec.params.get("icon", "🚨")
                instrucao = spec.params.get(
                    "prompt",
                    f"Alerta do monitor '{spec.name}' — detectado agora, avise o chefe "
                    "de forma útil e sugira o que fazer",
                )
                answer = await self.brain.ask(f"[{instrucao}] {detail}")
                await self.send(f"{icone} {answer}", urgency=urgencia)
                if spec.params.get("uma_vez") and self.remover(spec.name):
                    await self.send(f"✔️ Era isso que eu estava vigiando — "
                                    f"encerrei o monitor '{spec.name}'.")
            elif not problem and previous is True and spec.params.get("notify_recovery", True):
                await self.send(f"✅ Monitor '{spec.name}': normalizado ({detail}).")
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
