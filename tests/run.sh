#!/usr/bin/env bash
set -uo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

status=0

echo "== Python (razer_api.py) =="
python3 -m unittest tests.test_razer_api -v || status=1

echo
echo "== JS (razer_api.js) =="
node --test tests/razer_api.test.js || status=1

echo
echo "== Bash (cleanup.sh secure_remove) =="
bash tests/test_cleanup_sh.sh || status=1

exit $status
