#!/usr/bin/env python3
"""Manda o backup para o Google Drive e mantém só os N mais recentes.

Usa o escopo drive.file: o app só enxerga arquivos que ele mesmo criou, então
não tem como mexer no resto do Drive do chefe — nem para ler, nem para apagar.

Uso: .venv/bin/python scripts/backup_drive.py <arquivo.tar.gz> [--manter 3]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from friday.tools.google_workspace import _service, accounts, token_file  # noqa: E402

PASTA = "JARVIS Backups"
MANTER_PADRAO = 3
ESCOPO_ESCRITA = "drive.file"


def _conta_que_pode_subir(preferida: str = "") -> str:
    """Nem toda conta conectada autorizou escrita no Drive — escolher a
    primeira da lista subiria o backup para uma conta sem permissão e o erro
    só apareceria no cron de domingo."""
    if preferida:
        return preferida
    for nome in accounts():
        try:
            if ESCOPO_ESCRITA in token_file(nome).read_text(encoding="utf-8"):
                return nome
        except OSError:
            continue
    return ""


def _pasta_id(drive) -> str:
    achados = drive.files().list(
        q=f"name = '{PASTA}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        fields="files(id)", pageSize=1,
    ).execute().get("files", [])
    if achados:
        return achados[0]["id"]
    criada = drive.files().create(
        body={"name": PASTA, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    print(f"pasta '{PASTA}' criada no Drive")
    return criada["id"]


def main():
    from googleapiclient.http import MediaFileUpload

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("uso: backup_drive.py <arquivo.tar.gz> [--manter N]")
    arquivo = Path(args[0]).expanduser()
    if not arquivo.exists():
        sys.exit(f"arquivo não encontrado: {arquivo}")

    manter = MANTER_PADRAO
    if "--manter" in sys.argv:
        manter = int(sys.argv[sys.argv.index("--manter") + 1])

    preferida = ""
    if "--conta" in sys.argv:
        preferida = sys.argv[sys.argv.index("--conta") + 1]
    conta = _conta_que_pode_subir(preferida)
    if not conta:
        sys.exit(
            "nenhuma conta Google autorizou escrita no Drive. Rode:\n"
            "  .venv/bin/python scripts/google_auth.py <conta>\n"
            f"contas conectadas: {', '.join(accounts()) or 'nenhuma'}"
        )

    drive = _service("drive", "v3", conta)
    pasta = _pasta_id(drive)

    enviado = drive.files().create(
        body={"name": arquivo.name, "parents": [pasta]},
        media_body=MediaFileUpload(str(arquivo), resumable=True),
        fields="id,name,size",
    ).execute()
    tamanho = int(enviado.get("size") or arquivo.stat().st_size)
    print(f"enviado: {enviado['name']} ({tamanho // 1024} KB) para '{PASTA}' na conta {conta}")

    # Poda: mantém os N mais recentes e apaga o resto (só arquivos nossos).
    existentes = drive.files().list(
        q=f"'{pasta}' in parents and trashed = false",
        fields="files(id,name,createdTime)", orderBy="createdTime desc", pageSize=100,
    ).execute().get("files", [])

    for velho in existentes[manter:]:
        drive.files().delete(fileId=velho["id"]).execute()
        print(f"removido do Drive (excedeu {manter}): {velho['name']}")

    print(f"backups no Drive agora: {min(len(existentes), manter)}")


if __name__ == "__main__":
    main()
