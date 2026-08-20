"""Google Drive: buscar e ler arquivos das contas do chefe.

Google Docs/Sheets/Slides são exportados (texto, CSV, PDF); PDFs e imagens
vão como mídia nativa para o Gemini enxergar, pelo mesmo mecanismo do
read_document. Somente leitura — o escopo pedido é drive.readonly.
"""

from __future__ import annotations

import asyncio
import io

from . import Tool, ToolContext, ToolMedia, truncate
from .google_workspace import NEED_ONE, _pick, _pick_one, _service, accounts

MAX_BYTES = 20 * 1024 * 1024

# Formato nativo do Google -> como trazer o conteúdo
EXPORTS = {
    "application/vnd.google-apps.document": ("text/plain", None),
    "application/vnd.google-apps.spreadsheet": ("text/csv", None),
    "application/vnd.google-apps.presentation": ("application/pdf", "application/pdf"),
    "application/vnd.google-apps.drawing": ("image/png", "image/png"),
}

# Binários que o Gemini lê direto
MEDIA_MIMES = ("application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif")


def _baixar(account: str, file_id: str):
    """(nome, mime, bytes) — exportando quando é formato nativo do Google."""
    from googleapiclient.http import MediaIoBaseDownload

    drive = _service("drive", "v3", account)
    meta = drive.files().get(fileId=file_id, fields="name,mimeType,size").execute()
    mime = meta.get("mimeType", "")
    tamanho = int(meta.get("size") or 0)
    if tamanho > MAX_BYTES:
        raise ValueError(f"arquivo grande demais ({tamanho // 1_000_000} MB; limite 20 MB)")

    if mime in EXPORTS:
        export_mime, mime_final = EXPORTS[mime]
        pedido = drive.files().export_media(fileId=file_id, mimeType=export_mime)
        mime = mime_final or export_mime
    else:
        pedido = drive.files().get_media(fileId=file_id)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, pedido)
    concluido = False
    while not concluido:
        _, concluido = downloader.next_chunk()
    return meta.get("name", file_id), mime, buffer.getvalue()


def _buscar(account: str, query: str, max_results: int) -> str:
    drive = _service("drive", "v3", account)
    escapado = query.replace("'", "\\'")
    resp = (
        drive.files()
        .list(
            q=f"(name contains '{escapado}' or fullText contains '{escapado}') and trashed = false",
            fields="files(id,name,mimeType,modifiedTime,owners(emailAddress))",
            pageSize=max_results,
            orderBy="modifiedTime desc",
        )
        .execute()
    )
    arquivos = resp.get("files", [])
    if not arquivos:
        return f"Nada encontrado para {query!r}."
    linhas = []
    for f in arquivos:
        tipo = f["mimeType"].replace("application/vnd.google-apps.", "Google ")
        linhas.append(f"{f['name']} — {tipo} — modificado {f.get('modifiedTime', '?')[:10]} — id: {f['id']}")
    return "\n".join(linhas)


async def _search(ctx: ToolContext, query: str, max_results: int = 10, account: str = "") -> str:
    nomes = _pick(account)
    if isinstance(nomes, str):
        return nomes
    secoes = []
    for nome in nomes:
        try:
            saida = await asyncio.to_thread(_buscar, nome, query, int(max_results))
        except Exception as exc:
            saida = f"erro nesta conta: {type(exc).__name__}: {exc}"
        secoes.append(f"[conta {nome}]\n{saida}" if len(nomes) > 1 else saida)
    return truncate("\n\n".join(secoes))


async def _read(ctx: ToolContext, file_id: str, account: str = "") -> str | ToolMedia:
    nome_conta = _pick_one(account)
    if not nome_conta:
        return NEED_ONE.format(", ".join(accounts()))
    try:
        nome, mime, dados = await asyncio.to_thread(_baixar, nome_conta, file_id)
    except Exception as exc:
        return f"Não consegui ler o arquivo: {type(exc).__name__}: {exc}"

    if mime in MEDIA_MIMES:
        return ToolMedia(
            note=f"Arquivo '{nome}' do Drive anexado ({len(dados) // 1024} KB) — leia e atenda ao pedido.",
            data=dados,
            mime=mime,
        )
    try:
        return f"[{nome}]\n" + truncate(dados.decode("utf-8"), 30000)
    except UnicodeDecodeError:
        return f"O arquivo '{nome}' é do tipo {mime}, que eu não sei exibir como texto."


SEARCH_TOOL = Tool(
    declaration={
        "name": "drive_search",
        "description": (
            "Busca arquivos no Google Drive do chefe por nome ou conteúdo "
            "(todas as contas conectadas, ou uma específica)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Termo de busca."},
                "max_results": {"type": "INTEGER", "description": "Máximo por conta (padrão 10)."},
                "account": {"type": "STRING", "description": "Conta Google (opcional)."},
            },
            "required": ["query"],
        },
    },
    handler=_search,
)

READ_TOOL = Tool(
    declaration={
        "name": "drive_read",
        "description": (
            "Lê um arquivo do Drive pelo id retornado por drive_search. Google Docs vira "
            "texto, Sheets vira CSV, e PDFs/imagens você enxerga diretamente."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_id": {"type": "STRING", "description": "Id do arquivo."},
                "account": {"type": "STRING", "description": "Conta dona do arquivo (obrigatória se houver várias)."},
            },
            "required": ["file_id"],
        },
    },
    handler=_read,
)
