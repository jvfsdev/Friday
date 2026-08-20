"""Autoriza a Friday numa conta Google — rodar UMA vez POR CONTA, numa máquina com navegador.

Preparação (uma vez só, vale para todas as contas):
1. No Google Cloud Console (console.cloud.google.com): crie um projeto,
   ative as APIs "Gmail API" e "Google Calendar API", configure a tela de
   consentimento OAuth (tipo Externo, adicione cada email seu como usuário
   de teste) e crie uma credencial "ID do cliente OAuth" do tipo
   "App para computador".
2. Baixe o JSON da credencial e salve como state/google_credentials.json

Depois, para cada conta:
   .venv/bin/python scripts/google_auth.py [nome]    # padrão: "principal"
   ex.: scripts/google_auth.py trabalho
Faça login no navegador COM A CONTA CERTA. O token fica em
state/google_token_<nome>.json (no deploy, copie a pasta state/ junto).


IMPORTANTE: rode com o Python do projeto — `.venv/bin/python scripts/google_auth.py`
(o python3 do sistema não enxerga as dependências).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from friday.tools.google_workspace import CREDENTIALS_FILE, SCOPES, STATE_DIR, token_file

PORTA = 8971  # porta do túnel SSH no modo sem navegador


def main():
    if not CREDENTIALS_FILE.exists():
        sys.exit(
            f"Credencial não encontrada em {CREDENTIALS_FILE}.\n"
            "Siga os passos do cabeçalho deste script (ou DEPLOY.md) primeiro."
        )
    name = sys.argv[1] if len(sys.argv) > 1 else "principal"

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)

    # Sem navegador (servidor): abre numa porta fixa e o login acontece no
    # navegador de outra máquina, através de um túnel SSH.
    sem_navegador = "--no-browser" in sys.argv or not (
        os.environ.get("DISPLAY") or sys.platform == "darwin"
    )
    if sem_navegador:
        print(
            f"\nSem navegador nesta máquina. Faça assim:\n"
            f"  1. No SEU computador, abra um túnel:\n"
            f"     ssh -L {PORTA}:localhost:{PORTA} {os.environ.get('USER', 'jarvis')}@<este-servidor>\n"
            f"  2. Deixe esse túnel aberto e abra no navegador a URL que vou imprimir agora.\n"
        )
        creds = flow.run_local_server(
            port=PORTA, open_browser=False, bind_addr="127.0.0.1",
            authorization_prompt_message="Abra esta URL no navegador:\n{url}\n",
        )
    else:
        creds = flow.run_local_server(port=0)
    STATE_DIR.mkdir(exist_ok=True)
    token_file(name).write_text(creds.to_json(), encoding="utf-8")
    print(f"Pronto! Conta Google '{name}' conectada ({token_file(name)}).")
    print("Para conectar outra conta: scripts/google_auth.py outro-nome")


if __name__ == "__main__":
    main()
