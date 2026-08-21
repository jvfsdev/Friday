#!/usr/bin/env bash
# Backup semanal completo do JARVIS, com cópia no Google Drive.
#
# Roda como ROOT (é o único jeito de ler o config do Home Assistant), mas o
# envio ao Drive volta a ser o usuário do JARVIS: o token do Google é dele, e
# root escrevendo nesse arquivo quebraria o refresh depois.
#
# Instalação (uma vez):
#   sudo crontab -e
#   0 3 * * 0 /home/jarvis/Friday/scripts/backup_semanal.sh >> /var/log/jarvis-backup.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CASA="$(cd "$ROOT/.." && pwd)"
DONO="$(stat -c '%U' "$ROOT")"
DESTINO="$CASA/backups-jarvis"

echo "=== $(date '+%Y-%m-%d %H:%M') — backup semanal ==="

bash "$ROOT/scripts/backup.sh" "$DESTINO"
chown -R "$DONO":"$DONO" "$DESTINO"

ULTIMO="$(ls -1t "$DESTINO"/jarvis-backup-*.tar.gz | head -1)"
su "$DONO" -c "$ROOT/.venv/bin/python $ROOT/scripts/backup_drive.py '$ULTIMO' --manter 3"

echo "=== concluído ==="
