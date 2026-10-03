#!/usr/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash -n PKGBUILD captureage tools/validate.sh
shellcheck captureage tools/validate.sh
desktop-file-validate captureage.desktop
python3 -m unittest discover -s tests -v
diff -u .SRCINFO <(makepkg --printsrcinfo)
makepkg --verifysource --force
makepkg --nodeps --nocheck --force
report=$(namcap PKGBUILD captureage-bin-*.pkg.tar.zst)
printf '%s\n' "$report"
if [[ "$report" == *' E: '* ]]; then
  exit 1
fi
