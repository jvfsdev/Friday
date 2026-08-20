"""Trabalhos longos em segundo plano.

Uma tarefa de código pode levar 20 minutos; o cérebro não pode ficar preso
esperando. O JARVIS responde "estou cuidando disso" na hora e avisa quando
termina, com o resultado.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

log = logging.getLogger("friday.jobs")

MAX_HISTORICO = 40


@dataclass
class Job:
    id: str
    kind: str
    description: str
    status: str = "rodando"          # rodando | concluído | falhou
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: str = ""

    def resumo(self) -> str:
        idade = int((self.finished_at or time.time()) - self.created_at)
        return f"[{self.id}] {self.kind}: {self.description} — {self.status} ({idade}s)"


class JobRegistry:
    def __init__(self, notify=None):
        self.jobs: dict[str, Job] = {}
        self.notify = notify  # async (texto) -> None

    def submit(self, kind: str, description: str, factory) -> Job:
        """`factory` é uma função sem argumentos que devolve a corrotina."""
        job = Job(id=uuid.uuid4().hex[:6], kind=kind, description=description)
        self.jobs[job.id] = job
        self._podar()
        asyncio.create_task(self._executar(job, factory))
        log.info("job %s iniciado: %s", job.id, description)
        return job

    async def _executar(self, job: Job, factory):
        try:
            job.result = await factory()
            job.status = "concluído"
        except asyncio.CancelledError:
            job.status = "cancelado"
            raise
        except Exception as exc:
            log.exception("job %s falhou", job.id)
            job.status = "falhou"
            job.result = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = time.time()

        if self.notify:
            icone = "✅" if job.status == "concluído" else "⚠️"
            try:
                await self.notify(f"{icone} Trabalho [{job.id}] {job.status}: {job.description}\n\n{job.result}")
            except Exception:
                log.exception("não consegui avisar sobre o job %s", job.id)

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def listar(self) -> str:
        if not self.jobs:
            return "Nenhum trabalho em andamento ou recente."
        recentes = sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)[:10]
        return "\n".join(j.resumo() for j in recentes)

    def _podar(self):
        if len(self.jobs) <= MAX_HISTORICO:
            return
        antigos = sorted(self.jobs.values(), key=lambda j: j.created_at)
        for job in antigos[: len(self.jobs) - MAX_HISTORICO]:
            if job.status != "rodando":
                self.jobs.pop(job.id, None)
