#!/bin/bash
# Run a command once a day at a fixed time (container time zone, TZ).
#   daily.sh 02:00 /usr/local/bin/backup.sh
# A failed run is logged and retried the next day; it never stops the loop.
set -u
at="$1"
shift
while true; do
    now=$(date +%s)
    next=$(date -d "today $at" +%s)
    if [ "$next" -le "$now" ]; then
        next=$(date -d "tomorrow $at" +%s)
    fi
    echo "$(date -Iseconds) next run of '$*' at $(date -Iseconds -d "@$next")"
    sleep $((next - now))
    "$@" || echo "$(date -Iseconds) FAILED (exit $?): $*" >&2
done
