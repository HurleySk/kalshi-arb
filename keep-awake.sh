#!/bin/bash
DURATION="${1:-120}"
echo "Keeping Mac awake for ${DURATION} minutes (until $(date -v+${DURATION}M '+%H:%M'))..."
caffeinate -dims -t $((DURATION * 60))
echo "Done — sleep prevention ended."
