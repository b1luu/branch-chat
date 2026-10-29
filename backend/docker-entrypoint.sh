#!/bin/sh
# Wait for the database, bring the schema to head, then hand off to the CMD (uvicorn).
set -e
python -m app.db.migrate
exec "$@"
