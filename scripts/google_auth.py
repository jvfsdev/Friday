"""Autoriza a Friday no Gmail/Calendar (rodar UMA vez, numa máquina com navegador).

1. No Google Cloud Console (console.cloud.google.com): crie um projeto,
   ative as APIs "Gmail API" e "Google Calendar API", configure a tela de
   consentimento OAuth (tipo Externo, adicione seu email como usuário de teste)
   e crie uma credencial "ID do cliente OAuth" do tipo "App para computador".
2. Baixe o JSON da credencial e salve como state/google_credentials.json
3. Rode: .venv/bin/python scripts/google_auth.py
4. Faça login no navegador que abrir. O token fica em state/google_token.json
   (no deploy, copie a pasta state/ junto para o servidor).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from friday.tools.google_workspace import CREDENTIALS_FILE, SCOPES, STATE_DIR, TOKEN_FILE


def main():
    if not CREDENTIALS_FILE.exists():
        sys.exit(
            f"Credencial não encontrada em {CREDENTIALS_FILE}.\n"
            "Siga os passos do cabeçalho deste script (ou DEPLOY.md) primeiro."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    STATE_DIR.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"Pronto! Token salvo em {TOKEN_FILE}.")
    print("A Friday ganhou as ferramentas de email e agenda no próximo restart.")


if __name__ == "__main__":
    main()
