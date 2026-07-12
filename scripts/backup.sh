#!/usr/bin/env bash
# Backup da Friday: memória, tokens/estado, .env e config.
# Uso: scripts/backup.sh /caminho/do/destino
# Mantém os 7 backups mais recentes. Para nuvem, rode rclone sobre o destino.
set -euo pipefail

DEST="${1:?informe o diretório de destino}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$DEST"

STAMP="$(date +%Y%m%d-%H%M%S)"
ARQ="$DEST/friday-backup-$STAMP.tar.gz"

tar -czf "$ARQ" -C "$ROOT" \
  --exclude='state/voices' \
  memory state .env config.yaml 2>/dev/null || {
    # .env ou state podem não existir em instalações novas; tenta o que houver
    tar -czf "$ARQ" -C "$ROOT" $(cd "$ROOT" && ls -d memory state .env config.yaml 2>/dev/null)
  }

# rotação: mantém os 7 mais novos (portável entre GNU e BSD)
ls -1t "$DEST"/friday-backup-*.tar.gz 2>/dev/null | tail -n +8 | while read -r velho; do
  rm -f "$velho"
done

echo "backup ok: $ARQ ($(du -h "$ARQ" | cut -f1))"
