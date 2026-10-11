#!/bin/sh
# Rebuild the deck: lint, inline the kit into one file, screenshot every beat, print the PDF.
# Fails loudly: any step that fails stops the script with a non-zero exit.
set -eu
cd "$(dirname "$0")"
KIT=/Users/carl/CODELocalProjects/lifeos/design/kit.py
PW=${PW:-/Users/carl/CODELocalProjects/hyderabaddies/deck/video/pw/bin/python}
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
