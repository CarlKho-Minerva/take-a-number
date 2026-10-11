#!/bin/sh
# Rebuild the deck: lint, inline the kit into one file, screenshot every beat, print the PDF.
# Fails loudly: any step that fails stops the script with a non-zero exit.
set -eu
cd "$(dirname "$0")"
KIT=/Users/carl/CODELocalProjects/lifeos/design/kit.py
PW=${PW:-python3}
if ! "$PW" -c "import playwright" 2>/dev/null; then
  echo "FAIL: $PW cannot import playwright. Run again as: PW=/path/to/venv/bin/python deck/build.sh" >&2
  exit 1
fi
python3 - <<'PY'
import re, subprocess, pathlib, sys, tempfile
js = re.findall(r'<script>(.*?)</script>', pathlib.Path('src.html').read_text(), flags=re.S)[-1]
f = pathlib.Path(tempfile.gettempdir()) / 'tan_deck_check.js'
f.write_text(js)
r = subprocess.run(['node', '--check', str(f)], capture_output=True, text=True)
if r.returncode:
    sys.exit('FAIL node --check:\n' + r.stderr)
print('script syntax ok')
PY
python3 "$KIT" check src.html
python3 "$KIT" inline src.html -o index.html
"$PW" shoot.py "$@"
