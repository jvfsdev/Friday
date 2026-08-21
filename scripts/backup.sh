#!/usr/bin/env bash
# Backup do JARVIS: o que dói perder e não dá para reconstruir sozinho.
# Uso: scripts/backup.sh /caminho/do/destino
# Mantém os 7 backups mais recentes. Para nuvem, rode rclone sobre o destino.
#
# Fora de propósito: .venv (reinstalável), state/voices (modelos baixáveis) e
# o histórico do Home Assistant (389 MB de gráficos — perder não quebra nada).
set -euo pipefail

DEST="${1:?informe o diretório de destino}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$DEST"

STAMP="$(date +%Y%m%d-%H%M%S)"
ARQ="$DEST/jarvis-backup-$STAMP.tar.gz"
LISTA=$(mktemp)
trap 'rm -f "$LISTA"' EXIT

# Núcleo: memória, tokens, credenciais e configuração.
for item in memory state .env config.yaml; do
  [ -e "$ROOT/$item" ] && echo "$item" >> "$LISTA"
done

# Monta sem compressão para poder acrescentar, e comprime no fim: tar não
# acrescenta em arquivo já comprimido.
TAR="${ARQ%.gz}"
tar -cf "$TAR" -C "$ROOT" --exclude='state/voices' --exclude='*.pyc' -T "$LISTA"

# Extras que vivem fora do repositório. O banco de finanças vai COM a chave
# que o decifra — sem ela o arquivo cifrado não serve para nada.
EXTRAS=""
[ -d "$HOME/.openfinance-analyst" ] && EXTRAS="$EXTRAS .openfinance-analyst"
if [ -d "$HOME/homeassistant" ]; then
  [ -d "$HOME/homeassistant/.storage" ] && EXTRAS="$EXTRAS homeassistant/.storage"
  for y in "$HOME"/homeassistant/*.yaml; do
    [ -e "$y" ] && EXTRAS="$EXTRAS homeassistant/$(basename "$y")"
  done
fi
if [ -n "$EXTRAS" ]; then
  # shellcheck disable=SC2086
  tar -rf "$TAR" -C "$HOME" $EXTRAS
fi

gzip -f "$TAR"

# rotação: mantém os 7 mais novos (portável entre GNU e BSD)
ls -1t "$DEST"/jarvis-backup-*.tar.gz 2>/dev/null | tail -n +8 | while read -r velho; do
  rm -f "$velho"
done

echo "backup ok: $ARQ ($(du -h "$ARQ" | cut -f1))"
