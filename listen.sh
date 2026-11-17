#!/bin/sh
# Waits, using ~1 MB, until this idle session gets a message from Telegram (exit 2 wakes Claude with it)
# or the session gets busy again because you typed in it (the hook deletes the .start marker; exit 0).
# Started by run.sh after the Stop hook; $3 is run.sh's pid. If Claude Code kills the hook, we notice and leave
# (on Windows killing a process doesn't kill its children).
state="$1"
sid="$2"
parent="$3"
here=$(dirname "$0")
n=0
trap 'rm -f "$state/listening/$sid" "$state/listening/$sid.mark"; exit 0' TERM INT HUP
while :; do
  # Busy again, session closed or hook killed? Leave first, so a message arriving now waits for the next turn.
  if [ ! -f "$state/listening/$sid.start" ] || ! kill -0 "$parent" 2>/dev/null; then
    rm -f "$state/listening/$sid" "$state/listening/$sid.mark"
    exit 0
  fi
  if [ -f "$state/deliver/$sid.txt" ]; then
    cat "$state/deliver/$sid.txt" >&2
    rm -f "$state/deliver/$sid.txt" "$state/listening/$sid" "$state/listening/$sid.mark"
    exit 2
  fi
  touch "$state/listening/$sid"
  n=$((n + 1))
  if [ $((n % 30)) = 0 ]; then
    # The poller that reads Telegram crashed or was killed (its lock wasn't refreshed since our last look)? Start one.
    if [ ! -d "$state/poller.lock" ] || [ "$state/poller.lock" -ot "$state/listening/$sid.mark" ]; then
      sh "$here/run.sh" ensure >/dev/null 2>&1
    fi
    touch "$state/listening/$sid.mark"
  fi
  sleep "${CLAUDE_TELEGRAM_TICK:-2}"
done
