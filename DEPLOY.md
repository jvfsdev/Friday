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
Monitores de eventos (disco, serviços, máquinas on/offline): bloco `monitors:`
do `config.yaml` — exemplos comentados lá.

**Autoatualização**: com um remoto git configurado (ex.: repositório no
GitHub), basta dizer "Friday, atualiza você mesma" — ela faz git pull,
reinstala dependências e se reinicia (o systemd a ressuscita sozinho).

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
5. **Mais de uma conta Google?** Rode de novo com um nome:
   `scripts/google_auth.py trabalho` (e adicione esse email como usuário de
   teste no passo 2; logue com a conta certa no navegador). A Friday consulta
   todas quando você não especificar ("tenho email novo?") e pergunta qual
   usar quando precisar de uma só ("marca reunião...").
6. No deploy, copie a pasta `state/` para o servidor junto com o `.env`.

Nota: se você já tinha autorizado antes de a Friday ganhar o envio de emails,
rode o script de novo para conceder a permissão nova (vale para cada conta).

Nota de privacidade: com app em modo "teste" o token expira a cada 7 dias
(basta rodar o script de novo). Para não expirar, publique o app na tela de
consentimento (pode ficar "não verificado" — só você usa). E lembre-se: o
conteúdo dos emails passa pela API gratuita do Gemini.

## 6b. Emails @hotmail/@outlook/@live (Microsoft)

A Microsoft aposentou o acesso por senha em 2024, então usa-se a API oficial
(Graph) com um app registrado — burocracia única de ~5 minutos:

1. Em [portal.azure.com](https://portal.azure.com) → **Registros de aplicativo**
   → Novo registro. Nome: Friday; tipos de conta: **"Contas em qualquer
   diretório organizacional e contas pessoais da Microsoft"**; redirecionamento
   em branco.
2. No app criado: **Autenticação** → habilite **"Permitir fluxos de cliente
   público"** → Salvar.
3. Copie o **ID do aplicativo (cliente)** para `MS_CLIENT_ID` no `.env`.
4. Para cada conta: `.venv/bin/python scripts/microsoft_auth.py [nome]` —
   o script mostra um código; entre em microsoft.com/devicelogin (de qualquer
   aparelho, até o celular), digite o código e faça login. Funciona direto no
   servidor, sem navegador local.
5. Reinicie a Friday. ("tenho email novo?" passa a olhar Gmail E Hotmail.)

Nota: se você autorizou antes de a Friday ganhar o envio de emails, rode o
script de novo (por conta) para conceder a permissão Mail.Send.

**Erro "does not exist in tenant 'Microsoft Services'" ao entrar no portal?**
Contas pessoais sem diretório Azure próprio caem num tenant restrito da
Microsoft. Nessa ordem:
1. Tente numa **janela anônima** (descarta sessão/tenant antigo em cache).
2. O registro do app pode ser feito por **qualquer** conta Microsoft que
   consiga entrar no portal — não precisa ser a conta do email! Registre com
   outra conta sua; o login da conta @hotmail acontece só no passo 4
   (devicelogin), que não passa pelo portal.
3. Sem nenhuma conta que entre: crie uma conta Azure gratuita com a @hotmail
   em azure.microsoft.com/free (pede cartão para verificação, sem cobrança) —
   isso cria o diretório próprio e o portal passa a abrir.

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

## 9. Voz na sala (mic e alto-falantes do notebook)

1. Instale as dependências de áudio:
   `sudo apt install -y ffmpeg alsa-utils` e
   `.venv/bin/pip install -e ".[voice]"`.
2. Teste o hardware: `arecord -l` (mic aparece?), `speaker-test -t wav -c 2`
   (som sai?). Ajuste volume com `alsamixer`.
3. **Importante**: serviço de sistema não acessa o áudio da sessão. Migre para
   serviço de usuário:
   ```bash
   sudo systemctl disable --now friday
   mkdir -p ~/.config/systemd/user && cp deploy/friday.service ~/.config/systemd/user/
   # edite o arquivo: remova a linha User=
   loginctl enable-linger $USER
   systemctl --user daemon-reload && systemctl --user enable --now friday
   journalctl --user -u friday -f
   ```
4. No `config.yaml`: `voice: {enabled: true}` e reinicie.
5. Fale **"Hey Jarvis"** e, após o log de "listening", faça a pergunta.
   Sensibilidade: ajuste `wake_threshold` (menor = mais sensível).

**Trocar a wake word por um nome próprio** (quando escolher o nome dela):
crie conta gratuita em console.picovoice.ai → Porcupine → treine a palavra
em português → baixe o `.ppn`. Me chame para plugar (troca do openWakeWord
pelo Porcupine no `friday/voice.py`).

## 9b. Rosto na tela (pygame, sem navegador)

O orbe animado do JARVIS renderizado nativamente (~50 MB de RAM — feito para
o notebook velho). Estados (repouso/ouvindo/pensando/falando), legendas do
que ele entendeu/respondeu, HUD com relógio e monitores, e modo noturno
automático (no quiet_hours vira só um relógio fraco).

1. `.venv/bin/pip install --upgrade pip && .venv/bin/pip install -e ".[face]"`
   Se o pip não achar o pygame ("No matching distribution"), use o da distro:
   ```bash
   sudo apt install -y python3-pygame
   # recria o venv enxergando os pacotes do sistema:
   python3 -m venv --system-site-packages --upgrade .venv
   ```
2. Preview local: `python -m friday.face --window --demo` (ESC sai).
3. Como serviço (tela do notebook ligada direto, sem desktop):
   ```bash
   sudo usermod -aG video,render $USER   # acesso ao framebuffer (relogar depois)
   cp deploy/jarvis-face.service ~/.config/systemd/user/   # ajuste caminhos
   systemctl --user daemon-reload && systemctl --user enable --now jarvis-face
   ```
   Se o servidor tiver desktop gráfico, remova a linha `SDL_VIDEODRIVER=kmsdrm`
   do service.
4. Dica: desative o blank do console para a tela não apagar sozinha:
   `sudo sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/&consoleblank=0 /' /etc/default/grub && sudo update-grub`

## 10. Extras

- **Backup**: descomente `backup_dir` e a rotina `backup-diario` no
  `config.yaml`. Para copiar à nuvem, configure `rclone` e adicione ao
  script. Teste manual: `bash scripts/backup.sh /caminho/destino`.
- **Navegador de verdade** (sites com JavaScript):
  `.venv/bin/pip install -e ".[browser]" && .venv/bin/playwright install chromium`
  (~300 MB; avalie se o notebook aguenta — as ferramentas `browse`/
  `browse_screenshot` aparecem sozinhas quando instalado).
- **Listas no Notion**: crie a página da lista, compartilhe com a integração
  e diga a ela: "Friday, anota na memória: a lista do mercado é a página X
  do Notion" (ela acha o id com notion_search). Depois é só "anota leite".
- **Presença (requer HA)**: instale o app **Home Assistant Companion** no
  celular e conecte ao seu HA — surge a entidade `person.*`/device_tracker.
  Exemplos prontos de monitor `ha_state` e rotina com `only_if` estão
  comentados no `config.yaml`.

## 11. Open Finance (gastos, cartões, orçamentos) — via ponte MCP

Integra o [openfinance-analyst](https://github.com/meloluan/openfinance-analyst)
(servidor MCP + Pluggy). O JARVIS passa a responder "onde foi meu dinheiro?",
achar assinaturas recorrentes, acompanhar fatura/parcelas e orçamentos.
⚠️ Lembre: as análises passam pelo Gemini (free tier = dados podem treinar).

No servidor:
```bash
sudo apt install -y nodejs npm build-essential
git clone https://github.com/meloluan/openfinance-analyst.git ~/openfinance-analyst
cd ~/openfinance-analyst && npm install && npm run build
cd ~/Friday && .venv/bin/pip install -e ".[mcp]"
```

Na Pluggy (uma vez):
1. Crie conta em [dashboard.pluggy.ai](https://dashboard.pluggy.ai) → crie uma
   aplicação → copie `CLIENT_ID` e `CLIENT_SECRET`.
2. Em [meu.pluggy.ai](https://meu.pluggy.ai) conecte seus bancos (conector
   "MeuPluggy" — dados do próprio CPF, grátis sem prazo) e anote os item ids.
3. No `.env` do JARVIS:
   ```
   PLUGGY_CLIENT_ID=...
   PLUGGY_CLIENT_SECRET=...
   PLUGGY_ITEM_IDS=id1,id2
   ```
4. No `config.yaml`, descomente o bloco `mcp_servers:` (exemplo no
   config.example.yaml) e reinicie o serviço.
5. Teste: "JARVIS, sincroniza meus dados bancários" e depois
   "onde foi meu dinheiro esse mês?".

A ponte MCP é genérica: outros servidores MCP entram do mesmo jeito, só
adicionando blocos em `mcp_servers:`.

## Resumo do que a Friday ganha em cada passo

| Passo | Ela passa a conseguir |
|---|---|
| 3 | Viver 24/7, rotinas e lembretes a qualquer hora |
| 5 | "abre X no Mac", "roda Y no PC", "liga meu PC" |
| 6 | "tenho email novo?", "o que tenho amanhã?", "marca dentista terça 15h" (multi-contas Google) |
| 6b | Emails @hotmail/@outlook também (multi-contas) |
| 7 | "procura minhas notas de Z", "anota isso na página W" |
| 8 | "apaga a luz da sala", "anuncia o jantar nos Echo" |
