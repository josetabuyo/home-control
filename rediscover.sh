#!/bin/bash
# Runs device discovery and restarts the smarthome server only if IPs changed.
cd "$(dirname "$0")"
source .venv/bin/activate

python3 discover.py
EXIT=$?

if [ $EXIT -eq 2 ]; then
    echo "  → IPs changed, restarting smarthome…"
    launchctl stop com.josetabuyo.smarthome
fi
