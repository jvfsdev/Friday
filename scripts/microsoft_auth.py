"""Autoriza a Friday numa conta Microsoft (@hotmail/@outlook/@live) — rodar UMA vez por conta.

Preparação (uma vez só, vale para todas as contas):
1. Em https://portal.azure.com → "Registros de aplicativo" → "Novo registro".
   - Nome: Friday
   - Tipos de conta: "Contas em qualquer diretório organizacional e contas
     pessoais da Microsoft"
   - Redirecionamento: deixe em branco.
2. No app criado: "Autenticação" → habilite "Permitir fluxos de cliente
   público" (Allow public client flows) → Salvar.
3. Copie o "ID do aplicativo (cliente)" para MS_CLIENT_ID no .env.

Depois, para cada conta:
   .venv/bin/python scripts/microsoft_auth.py [nome]     # padrão: "principal"
O script mostra um código — entre em https://microsoft.com/devicelogin em
qualquer aparelho, digite o código e faça login (funciona até no servidor).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from friday.config import load_config
from friday.tools.outlook import AUTHORITY, SCOPES, STATE_DIR, cache_file


def main():
    config = load_config()
    if not config.ms_client_id:
        sys.exit("MS_CLIENT_ID não configurado no .env. Siga o cabeçalho deste script (ou DEPLOY.md).")

    name = sys.argv[1] if len(sys.argv) > 1 else "principal"
    import msal

    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(config.ms_client_id, authority=AUTHORITY, token_cache=cache)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        sys.exit(f"Falha ao iniciar o login: {flow.get('error_description', flow)}")

    print(flow["message"])  # instruções oficiais: URL + código
    result = app.acquire_token_by_device_flow(flow)  # bloqueia até você logar
    if "access_token" not in result:
        sys.exit(f"Login falhou: {result.get('error_description', result)}")

    STATE_DIR.mkdir(exist_ok=True)
    cache_file(name).write_text(cache.serialize(), encoding="utf-8")
    print(f"Pronto! Conta Microsoft '{name}' conectada ({cache_file(name)}).")
    print("A Friday ganha as ferramentas de Outlook no próximo restart.")


if __name__ == "__main__":
    main()
