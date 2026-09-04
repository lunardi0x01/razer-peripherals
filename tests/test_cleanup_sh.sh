#!/usr/bin/env bash
# Extracts and exercises cleanup.sh's secure_remove() in isolation. Unlike
# pair.sh, this function is fully local/deterministic (no live network
# calls), so it's safe to pull out and test directly with sed, same
# technique as test_pair_sh.sh uses for valid_ip.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CLEANUP_SH="$SCRIPT_DIR/../cleanup.sh"

eval "$(sed -n '/^secure_remove()/,/^}/p' "$CLEANUP_SH")"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail=0

# A regular, owned file gets its content zeroed before the path is removed.
# Use a hard link to keep the inode alive after secure_remove's rm, so the
# shredded (all-zero) content can still be inspected.
target="$TMP_DIR/regular.txt"
witness="$TMP_DIR/regular-witness.txt"
printf 'supersecretusername' > "$target"
ln "$target" "$witness"
secure_remove "$target"
if [[ -e "$target" ]]; then
  echo "FAIL secure_remove did not remove a regular file"
  fail=1
else
  echo "ok   secure_remove removed a regular file"
fi
if [[ "$(od -An -tx1 -- "$witness" | tr -d ' \n')" =~ ^0*$ ]]; then
  echo "ok   secure_remove zeroed the content before removing it"
else
  echo "FAIL secure_remove left non-zero content behind"
  fail=1
fi

# A symlink at the target path is removed itself, and the write never
# follows it into the file it points at.
decoy="$TMP_DIR/decoy.txt"
link="$TMP_DIR/link.txt"
printf 'do not touch' > "$decoy"
ln -s "$decoy" "$link"
secure_remove "$link"
if [[ -e "$link" || -L "$link" ]]; then
  echo "FAIL secure_remove did not remove a symlink"
  fail=1
else
  echo "ok   secure_remove removed a symlink"
fi
if [[ "$(cat -- "$decoy")" == "do not touch" ]]; then
  echo "ok   secure_remove left the symlink's target untouched"
else
  echo "FAIL secure_remove wrote through the symlink into its target"
  fail=1
fi

# A path that doesn't exist at all is a silent no-op, not an error.
if secure_remove "$TMP_DIR/does-not-exist.txt"; then
  echo "ok   secure_remove no-ops on a missing path"
else
  echo "FAIL secure_remove should not fail on a missing path"
  fail=1
fi

# A real directory at the target path is left alone rather than crashing
# the caller -- cleanup.sh runs under `set -euo pipefail`, and `rm -f` on a
# directory still exits non-zero despite -f, which previously aborted the
# whole script instead of the no-op the original (pre-refactor) behavior
# gave. Run this one in a fresh `set -e` subshell so it actually exercises
# that failure mode instead of just this test file's own laxer options.
dir_target="$TMP_DIR/a-directory"
mkdir -p "$dir_target"
if bash -c '
  set -euo pipefail
  eval "$(sed -n "/^secure_remove()/,/^}/p" "$1")"
  secure_remove "$2"
' _ "$CLEANUP_SH" "$dir_target"; then
  echo "ok   secure_remove does not abort the caller on a real directory"
else
  echo "FAIL secure_remove crashed the caller (set -e) on a real directory"
  fail=1
fi
if [[ -d "$dir_target" ]]; then
  echo "ok   secure_remove left the directory in place"
else
  echo "FAIL secure_remove should not have touched the directory"
  fail=1
fi

# A FIFO planted at the target path must not hang secure_remove (H-01):
# os.O_WRONLY without O_NONBLOCK blocks until a reader opens the same
# path, which never happens here. timeout catches a regression as a
# non-zero/124 exit rather than an actual indefinite hang.
fifo="$TMP_DIR/fifo.txt"
mkfifo "$fifo"
if timeout 3 bash -c '
  eval "$(sed -n "/^secure_remove()/,/^}/p" "$1")"
  secure_remove "$2"
' _ "$CLEANUP_SH" "$fifo"; then
  echo "ok   secure_remove did not hang on a FIFO"
else
  echo "FAIL secure_remove hung (or errored) on a FIFO"
  fail=1
fi

exit $fail
