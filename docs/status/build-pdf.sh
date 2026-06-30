#!/usr/bin/env bash
# Собирает PDF статуса PetBank из шаблона petbank-status-template.html.
# «Форма» (вёрстка/стили) фиксирована в шаблоне — каждый день обновляешь в нём
# цифры/текст и прогоняешь этот скрипт. Кириллицу и CSS рендерит headless Chrome.
#
# Использование:
#   ./docs/status/build-pdf.sh                 # → ~/Desktop/PetBank-status-ГГГГ-ММ-ДД.pdf
#   ./docs/status/build-pdf.sh /путь/out.pdf   # явный путь вывода
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$DIR/petbank-status-template.html"
OUT="${1:-$HOME/Desktop/PetBank-status-$(date +%F).pdf}"

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || CHROME="$(command -v google-chrome || command -v chromium || true)"
if [ -z "${CHROME:-}" ] || [ ! -x "$CHROME" ]; then
  echo "Не найден Chrome/Chromium для печати в PDF" >&2; exit 1
fi

"$CHROME" --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$OUT" "file://$SRC" >/dev/null 2>&1

echo "PDF готов: $OUT"
