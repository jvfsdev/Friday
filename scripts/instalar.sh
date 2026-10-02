#!/usr/bin/env bash
# Instala o Guará neste computador (Linux com systemd) e liga o painel.
#
#   git clone https://github.com/jvfsdev/Guara.git ~/Guara
#   cd ~/Guara && ./scripts/instalar.sh
#
# Depois é tudo pelo navegador: o script mostra o endereço do painel.
# Pode rodar de novo quantas vezes quiser — o que já existe é mantido.
set -euo pipefail

PASTA="$(cd "$(dirname "$0")/.." && pwd)"
USUARIO="$(id -un)"
cd "$PASTA"

ok()   { printf '  \033[1m✓\033[0m %s\n' "$1"; }
erro() { printf '\n  ✕ %s\n\n' "$1" >&2; exit 1; }

echo
echo "  Guará — instalação em $PASTA (usuário $USUARIO)"
echo

[ "$(id -u)" -eq 0 ] && erro "Rode como o seu usuário normal, não como root (o script pede sudo quando precisa)."
command -v systemctl >/dev/null || erro "Este instalador precisa de Linux com systemd."
PY="$(command -v python3 || true)"
[ -n "$PY" ] || erro "Instale o Python 3.11 ou mais novo (sudo apt install python3 python3-venv)."
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || erro "Python 3.11 ou mais novo é necessário (você tem $("$PY" --version))."

# 1. pacotes do sistema (ffmpeg para áudio; Inter é a fonte da identidade)
if command -v apt-get >/dev/null; then
  sudo apt-get install -y -q python3-venv ffmpeg fonts-inter >/dev/null && ok "pacotes do sistema"
fi

# 2. ambiente Python
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e . && ok "dependências do Guará"

# 3. arquivos de configuração (nunca sobrescreve os que já existem)
[ -f .env ] || cp .env.example .env
chmod 600 .env
[ -f config.yaml ] || cp config.example.yaml config.yaml
ok "configuração (.env e config.yaml)"

# 4. serviços: o Guará e o painel, com o usuário e a pasta DESTE computador
servico() {  # nome descrição comando
  sudo tee "/etc/systemd/system/$1.service" >/dev/null <<EOF
[Unit]
Description=$2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USUARIO
WorkingDirectory=$PASTA
ExecStart=$PASTA/.venv/bin/python -m $3
Restart=always
RestartSec=10
$4

[Install]
WantedBy=multi-user.target
EOF
}
servico friday "Guará - assistente pessoal" friday ""
servico guara-painel "Guará - painel de configuração" friday.painel "Nice=5
MemoryMax=150M"
sudo systemctl daemon-reload
sudo systemctl enable --now guara-painel >/dev/null 2>&1
# O Guará só sobe de verdade depois que o painel tiver a chave do Gemini e o
# Telegram; até lá o systemd tenta de novo sozinho a cada 10 s.
sudo systemctl enable --now friday >/dev/null 2>&1
ok "serviços instalados (sobem sozinhos quando o computador liga)"

# 5. onde abrir o painel
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "  Pronto. Abra no navegador do celular ou do computador, na mesma rede:"
echo
printf '      \033[1mhttp://%s:8080\033[0m\n' "${IP:-<ip-deste-computador>}"
echo
echo "  Crie a senha do painel e preencha os dois itens laranja (Gemini e Telegram)."
echo "  Voz na sala e rosto na tela são opcionais — veja o DEPLOY.md."
echo
