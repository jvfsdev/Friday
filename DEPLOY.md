# Guia de deploy da Friday no servidor 🚀

Checklist completo, na ordem. Os passos 1–4 colocam a Friday no ar; os demais
ligam cada capacidade e podem ser feitos aos poucos, em qualquer ordem.

## 1. Preparar o notebook (Linux)

```bash
sudo apt update && sudo apt install -y git python3 python3-venv   # Ubuntu/Debian
```

Requisitos: Python 3.11+ (`python3 --version`) e acesso à internet.
Dica: nas opções de energia, configure para **não suspender com a tampa fechada**
(`/etc/systemd/logind.conf` → `HandleLidSwitch=ignore`, depois
`sudo systemctl restart systemd-logind`).

## 2. Instalar a Friday

Leve o repositório para o servidor (git clone de um remoto, ou copie a pasta
via pendrive/scp — o `.env` NÃO vai no git, copie-o manualmente):

```bash
cd ~ && git clone <seu-remoto> Friday   # ou: scp -r do Mac
cd Friday
python3 -m venv .venv
.venv/bin/pip install -e .
cp /caminho/do/.env .env                # o mesmo .env que você preencheu no Mac
```

Se você já autorizou o Google no Mac (passo 6), copie a pasta `state/` também.

Teste rápido antes de virar serviço:

```bash
.venv/bin/python -m friday --cli
```

## 3. Rodar 24/7 (systemd)

Edite `deploy/friday.service` se seu usuário/caminho não for `jvfs`/`/home/jvfs/Friday`, então:

```bash
sudo cp deploy/friday.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now friday
```

⚠️ **Antes de iniciar no servidor, pare a Friday no Mac** (duas instâncias do
mesmo bot brigam pelas mensagens — erro "Conflict" no log).

Verificar: `systemctl status friday` e `journalctl -u friday -f`.
Teste do celular: mande uma mensagem no Telegram.

## 4. Manutenção do dia a dia

```bash
journalctl -u friday -f          # logs ao vivo
sudo systemctl restart friday    # reiniciar (após git pull ou mudar config)
```

Rotinas novas: edite o bloco `routines:` do `config.yaml` e reinicie.

## 5. Controlar o Mac e o PC (SSH + Wake-on-LAN)

No **servidor**: `ssh-keygen -t ed25519` (Enter em tudo, sem senha).

**Mac**: Ajustes → Geral → Compartilhamento → ligue **Sessão Remota**.
No servidor: `ssh-copy-id seu-usuario@ip-do-mac` e teste `ssh seu-usuario@ip-do-mac uptime`.

**PC Windows**: Configurações → Sistema → Recursos Opcionais → instalar
**Servidor OpenSSH**; em Serviços, inicie `OpenSSH SSH Server` e deixe como
Automático. Copie o conteúdo de `~/.ssh/id_ed25519.pub` do servidor para
`C:\Users\voce\.ssh\authorized_keys` (se sua conta for admin:
`C:\ProgramData\ssh\administrators_authorized_keys`).

**Wake-on-LAN** (ligar o PC à distância): ative "Wake on LAN"/"Magic Packet"
na BIOS e nas propriedades do adaptador de rede no Windows. Descubra o MAC
com `ipconfig /all` (Endereço Físico).

Por fim, descomente e preencha o bloco `machines:` no `config.yaml`
(IPs fixos ajudam: reserve-os no roteador) e reinicie a Friday.

## 6. Email e Agenda (Google)

Pode ser feito **no Mac antes do deploy** (precisa de navegador):

1. Em [console.cloud.google.com](https://console.cloud.google.com): crie um
   projeto → APIs e serviços → ative **Gmail API** e **Google Calendar API**.
2. Tela de consentimento OAuth: tipo **Externo**, adicione seu email como
   **usuário de teste**.
3. Credenciais → Criar credenciais → **ID do cliente OAuth** → tipo
   **App para computador** → baixe o JSON e salve como
   `state/google_credentials.json` (crie a pasta `state/` se não existir).
4. Rode `.venv/bin/python scripts/google_auth.py` e faça login no navegador.
5. No deploy, copie a pasta `state/` para o servidor junto com o `.env`.

Nota de privacidade: com app em modo "teste" o token expira a cada 7 dias
(basta rodar o script de novo). Para não expirar, publique o app na tela de
consentimento (pode ficar "não verificado" — só você usa). E lembre-se: o
conteúdo dos emails passa pela API gratuita do Gemini.

## 7. Notion

1. Em [notion.so/my-integrations](https://www.notion.so/my-integrations):
   nova integração interna → copie o token para `NOTION_TOKEN` no `.env`.
2. Em cada página/base que a Friday deve acessar: menu **•••** → Conexões →
   adicione a integração (as sub-páginas herdam o acesso).
3. Reinicie a Friday.

## 8. Casa inteligente (Home Assistant)

No servidor, instale Docker (`curl -fsSL https://get.docker.com | sh`) e:

```bash
docker run -d --name homeassistant --restart=unless-stopped \
  --network=host -e TZ=America/Sao_Paulo \
  -v ~/homeassistant:/config ghcr.io/home-assistant/home-assistant:stable
```

1. Abra `http://ip-do-servidor:8123` e crie a conta local.
2. Adicione seus dispositivos: Configurações → Dispositivos e serviços
   (lâmpadas/tomadas Tuya/SmartLife entram pela integração Tuya).
3. Para os Echo falarem pela Friday: instale o [HACS](https://hacs.xyz) e,
   por ele, a integração **Alexa Media Player** (login com a conta Amazon).
4. Crie um token: seu Perfil no HA → Segurança → **Tokens de acesso de longa
   duração** → cole em `HA_TOKEN` no `.env` (confira `HA_URL`).
5. Reinicie a Friday e teste: "Friday, quais dispositivos você vê na casa?"

## Resumo do que a Friday ganha em cada passo

| Passo | Ela passa a conseguir |
|---|---|
| 3 | Viver 24/7, rotinas e lembretes a qualquer hora |
| 5 | "abre X no Mac", "roda Y no PC", "liga meu PC" |
| 6 | "tenho email novo?", "o que tenho amanhã?", "marca dentista terça 15h" |
| 7 | "procura minhas notas de Z", "anota isso na página W" |
| 8 | "apaga a luz da sala", "anuncia o jantar nos Echo" |
