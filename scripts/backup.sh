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
# A casa é a pasta que contém o repositório — NÃO $HOME, que vira /root
# quando o backup roda com sudo (e aí os extras simplesmente sumiam).
CASA="$(cd "$ROOT/.." && pwd)"

EXTRAS=""
[ -d "$CASA/.openfinance-analyst" ] && EXTRAS="$EXTRAS .openfinance-analyst"
if [ -d "$CASA/homeassistant" ]; then
  [ -d "$CASA/homeassistant/.storage" ] && EXTRAS="$EXTRAS homeassistant/.storage"
  for y in "$CASA"/homeassistant/*.yaml; do
    [ -e "$y" ] && EXTRAS="$EXTRAS homeassistant/$(basename "$y")"
  done
fi
# O Home Assistant roda como root e seus arquivos não são legíveis pelo
# usuário do JARVIS. Em vez de falhar o backup inteiro por causa disso,
# avisamos e seguimos: o núcleo (tokens, memória, config) é o essencial.
FALTOU=""
for extra in $EXTRAS; do
  if tar -rf "$TAR" -C "$CASA" "$extra" 2>/dev/null; then
    continue
  fi
  FALTOU="$FALTOU $extra"
done

gzip -f "$TAR"

# rotação: mantém os 7 mais novos (portável entre GNU e BSD)
ls -1t "$DEST"/jarvis-backup-*.tar.gz 2>/dev/null | tail -n +8 | while read -r velho; do
  rm -f "$velho"
done

echo "backup ok: $ARQ ($(du -h "$ARQ" | cut -f1))"
if [ -n "$FALTOU" ]; then
  echo "AVISO: sem permissão para ler:$FALTOU" >&2
  echo "       (rode como root para incluir — veja DEPLOY.md)" >&2
fi
