#!/bin/bash
# Renders the invented case documents in cases/files/src to the files the demo uploads:
# six PDFs (print) and one JPEG that looks like a phone photo. Needs Google Chrome and macOS sips.
#   bash tools/render_case_files.sh
set -euo pipefail
cd "$(dirname "$0")/../cases/files"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
for name in lease_and_statement naranjo_v_spectrum_s279397 offense_report collision_report purchase_order_and_inspection registration_certificate; do
  rm -f "$name.pdf"   # derived file, rebuilt from src on every run
  "$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf="$PWD/$name.pdf" "file://$PWD/src/$name.html" 2>/dev/null
done
rm -f notice_letter.png notice_letter.jpg
"$CHROME" --headless=new --disable-gpu --hide-scrollbars --window-size=1000,1250 --screenshot="$PWD/notice_letter.png" "file://$PWD/src/notice_letter.html" 2>/dev/null
sips -s format jpeg -s formatOptions 82 notice_letter.png --out notice_letter.jpg >/dev/null
rm -f notice_letter.png
ls -la *.pdf *.jpg | awk '{print $5, $9}'
