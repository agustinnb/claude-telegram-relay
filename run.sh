#!/bin/sh
# Finds a real Python 3.8+: python3 on macOS/Linux, the py launcher or python on Windows.
# `py -3` ignores the script's shebang; the -c check skips the Microsoft Store stub.
here=$(dirname "$0")
for py in python3 "py -3" python; do
  if $py -c "import sys; sys.exit(sys.version_info < (3, 8))" >/dev/null 2>&1; then
    out=$($py "$here/claude_telegram.py" "$@")
    rc=$?
    if [ "$rc" = 10 ]; then
      # Stop hook asked to keep listening: hand over to a tiny shell loop instead of a ~20 MB Python process.
      # Not exec: the loop watches this pid, so it ends when Claude Code kills the hook (needed on Windows).
      state=$(printf '%s\n' "$out" | sed -n 1p | tr -d '\r')  # Windows Python ends lines with \r\n
      sid=$(printf '%s\n' "$out" | sed -n 2p | tr -d '\r')
      trap 'kill "$child" 2>/dev/null; exit 0' TERM INT HUP
      sh "$here/listen.sh" "$state" "$sid" "$$" &
      child=$!
      wait "$child"
      exit $?
    fi
    [ -n "$out" ] && printf '%s\n' "$out"
    exit "$rc"
  fi
done
echo "telegram-relay: Python 3.8+ not found on PATH" >&2
exit 0
