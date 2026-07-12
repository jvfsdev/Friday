"""Leitura de documentos (PDF, imagens, texto) — locais ou de outra máquina via SFTP.

Arquivos que o Gemini lê nativamente (PDF/imagem) vão como mídia (ToolMedia);
arquivos de texto vão como texto mesmo, sem gastar tokens de mídia.
"""

from __future__ import annotations

from pathlib import Path

import asyncssh

from . import Tool, ToolContext, ToolMedia, truncate

MAX_BYTES = 20 * 1024 * 1024  # 20 MB

MEDIA_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

TEXT_EXTS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".log",
    ".py", ".js", ".ts", ".sh", ".ini", ".toml", ".conf",
}


async def _fetch_remote(ctx: ToolContext, machine: str, path: str) -> bytes | str:
    spec = ctx.config.machines.get(machine)
    if not spec:
        known = ", ".join(ctx.config.machines) or "nenhuma"
        return f"Máquina '{machine}' não configurada. Máquinas conhecidas: {known}."
    try:
        async with asyncssh.connect(
            spec.host, username=spec.user, known_hosts=None, connect_timeout=15
        ) as conn:
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(path, "rb") as f:
                    return await f.read(MAX_BYTES + 1)
    except (OSError, asyncssh.Error) as exc:
        return f"Falha ao buscar {path} em {machine}: {exc}"


async def _handler(ctx: ToolContext, path: str, machine: str = "") -> str | ToolMedia:
    expanded = Path(path).expanduser()
    if machine:
        data = await _fetch_remote(ctx, machine, path)
        if isinstance(data, str):
            return data
        origem = f"{machine}:{path}"
    else:
        if not expanded.exists():
            return f"Arquivo não encontrado: {expanded}"
        if expanded.stat().st_size > MAX_BYTES:
            return f"Arquivo grande demais ({expanded.stat().st_size // 1_000_000} MB; limite 20 MB)."
        data = expanded.read_bytes()
        origem = str(expanded)

    if len(data) > MAX_BYTES:
        return "Arquivo grande demais (limite 20 MB)."

    ext = Path(path).suffix.lower()
    if ext in MEDIA_MIME:
        return ToolMedia(
            note=f"Documento '{origem}' anexado ({len(data) // 1024} KB) — leia-o e atenda ao pedido.",
            data=data,
            mime=MEDIA_MIME[ext],
        )
    if ext in TEXT_EXTS or not ext:
        try:
            return truncate(data.decode("utf-8"), 30000)
        except UnicodeDecodeError:
            return f"O arquivo {origem} não é texto UTF-8 nem um formato que eu saiba exibir ({ext})."
    return (
        f"Extensão {ext} não suportada para leitura direta. Suportados: "
        f"{', '.join(sorted(MEDIA_MIME))} e arquivos de texto."
    )


TOOL = Tool(
    declaration={
        "name": "read_document",
        "description": (
            "Lê um arquivo para análise: PDF e imagens são enxergados nativamente; "
            "texto/código vem como texto. Local (servidor) ou de outra máquina via SFTP."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING", "description": "Caminho do arquivo (ex.: ~/Downloads/contrato.pdf)."},
                "machine": {"type": "STRING", "description": "Máquina remota (ex.: 'mac', 'pc'); vazio = local."},
            },
            "required": ["path"],
        },
    },
    handler=_handler,
)
