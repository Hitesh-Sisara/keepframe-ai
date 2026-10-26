#!/usr/bin/env bash
# Render docs/REPORT.md -> docs/REPORT.pdf. Uses pandoc (+ a PDF engine) if present, else md-to-pdf via npx (headless Chrome).
set -euo pipefail
cd "$(dirname "$0")/../docs"
if command -v pandoc >/dev/null 2>&1 && command -v wkhtmltopdf >/dev/null 2>&1; then
  pandoc REPORT.md --standalone --css report.css --pdf-engine=wkhtmltopdf --resource-path=. -o REPORT.pdf
else
  CHROME="${PUPPETEER_EXECUTABLE_PATH:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}"
  if [ -n "$CHROME" ]; then export PUPPETEER_EXECUTABLE_PATH="$CHROME" PUPPETEER_SKIP_DOWNLOAD=1; fi
  npx -y md-to-pdf@5 --stylesheet "$PWD/report.css" \
    --launch-options '{"args":["--no-sandbox"]}' \
    --pdf-options '{"format":"A4","margin":{"top":"14mm","bottom":"14mm","left":"13mm","right":"13mm"},"printBackground":true}' \
    REPORT.md </dev/null   # md-to-pdf reads stdin when it is not a TTY; close it
fi
pages=$(python3 -c "import re;print(len(re.findall(rb'/Type\s*/Page[^s]',open('REPORT.pdf','rb').read())))")
echo "docs/REPORT.pdf: ${pages} pages (limit 12)"
[ "$pages" -le 12 ] || { echo "REPORT.pdf exceeds 12 pages" >&2; exit 1; }
