#!/bin/sh
# Points everything that changes at the persistent volume mounted on /app/data, then starts the agents.
set -e
cd /app

mkdir -p data/runs data/logs

# First start on an empty volume: seed it with the tracker history and settings baked into the image.
cp -rn /app/seed_data/. /app/data/ 2>/dev/null || true
[ -f data/config.json ] || cp /app/config.json data/config.json

# config.json is rewritten by the poller (mode and pause changes), so it has to live on the volume too.
ln -sf /app/data/config.json /app/config.json
rm -rf /app/runs /app/logs
ln -s /app/data/runs /app/runs
ln -s /app/data/logs /app/logs

exec python run_all.py
