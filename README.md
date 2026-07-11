# Friday 🤖

Assistente pessoal autônoma auto-hospedada, inspirada na FRIDAY do Homem de Ferro.

- **Cérebro**: Gemini Flash (API gratuita) com function calling
- **Interface**: bot no Telegram (só responde ao dono) + modo `--cli` para testes
- **Mora em**: um servidor Linux, como serviço systemd 24/7
- **Faz**: comandos no servidor, controla outras máquinas via SSH (e liga por Wake-on-LAN), pesquisa na web, memória persistente, rotinas proativas e lembretes, casa inteligente via Home Assistant

## Instalação (no Mac para testar, ou direto no servidor)

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env   # e preencha (ver abaixo)
```

### 1. Chave do Gemini (grátis)
1. Acesse https://aistudio.google.com/apikey logado na sua conta Google.
2. "Create API key" → cole em `GEMINI_API_KEY` no `.env`.

### 2. Bot do Telegram
1. No Telegram, fale com o **@BotFather** → `/newbot` → escolha nome e username.
2. Cole o token em `TELEGRAM_BOT_TOKEN`.
3. Fale com o **@userinfobot** para descobrir seu ID numérico → `TELEGRAM_USER_ID`.
4. Mande um `/start` para o seu bot uma vez (habilita a Friday a te mandar mensagens proativas).

### 3. Testar

```bash
.venv/bin/python -m friday --cli   # chat no terminal
.venv/bin/python -m friday          # modo bot do Telegram
```

## Deploy no servidor Linux

```bash
git clone <este repo> ~/Friday && cd ~/Friday
python3 -m venv .venv && .venv/bin/pip install -e .
cp .env.example .env && nano .env
sudo cp deploy/friday.service /etc/systemd/system/   # ajuste User/caminhos no arquivo
sudo systemctl daemon-reload
sudo systemctl enable --now friday
journalctl -u friday -f   # acompanhar os logs
```

## Controlar o Mac e o PC (SSH)

No **servidor**, gere uma chave: `ssh-keygen -t ed25519` (sem senha).

- **Mac**: Ajustes → Geral → Compartilhamento → **Sessão Remota** (ligar). Depois, do servidor: `ssh-copy-id usuario@ip-do-mac`.
- **Windows**: Configurações → Sistema → Recursos Opcionais → adicionar **Servidor OpenSSH**; inicie o serviço `sshd` (e deixe automático). Copie a chave pública do servidor para `C:\Users\voce\.ssh\authorized_keys` (para conta admin: `C:\ProgramData\ssh\administrators_authorized_keys`).
- Descomente e preencha o bloco `machines:` no `config.yaml`. Para Wake-on-LAN, ative "Wake on LAN/Magic Packet" na BIOS e no driver de rede do PC e preencha `mac_address`.

## Casa inteligente (Home Assistant)

No servidor (requer Docker):

```bash
docker run -d --name homeassistant --restart=unless-stopped \
  --network=host -e TZ=America/Sao_Paulo \
  -v ~/homeassistant:/config ghcr.io/home-assistant/home-assistant:stable
```

1. Abra `http://ip-do-servidor:8123`, crie a conta e adicione seus dispositivos (Tuya/SmartLife etc. são detectados ou adicionados por integração).
2. Para os Echo/Alexa: instale o **HACS** e a integração **Alexa Media Player** (dá voz à Friday pelos Echo).
3. No HA: Perfil → Segurança → **Tokens de acesso de longa duração** → crie um e cole em `HA_TOKEN` no `.env` (e ajuste `HA_URL` se preciso).

## Rotinas e lembretes

Rotinas fixas ficam no `config.yaml` (cron → instrução). Lembretes dinâmicos você pede na conversa: *"me lembra às 15h de tirar a roupa da máquina"*.

## Notas

- O nível gratuito do Gemini tem limites de requisições por minuto/dia; a Friday espera e tenta de novo quando esbarra neles. Atenção: no nível gratuito o Google pode usar seus dados para treinar modelos — pese isso antes de conectar e-mail (Fase 6). Ativar cobrança no projeto remove o uso para treino, mas aí passa a pagar por uso.
- Comandos perigosos (rm, sudo, shutdown…) pedem confirmação com botões no Telegram antes de executar.
