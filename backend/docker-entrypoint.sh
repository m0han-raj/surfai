#!/bin/sh
# Wait for PostgreSQL, apply migrations, then hand off to the CMD.
set -e

echo "[surfai] waiting for the database..."
attempt=0
until python -c "
import sys
from sqlalchemy import create_engine, text
from app.config import settings
try:
    create_engine(settings.database_url).connect().execute(text('SELECT 1'))
except Exception as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 40 ]; then
        echo "[surfai] database did not become ready in time" >&2
        exit 1
    fi
    sleep 1
done
echo "[surfai] database is ready"

echo "[surfai] applying migrations"
alembic upgrade head

echo "[surfai] starting: $*"
exec "$@"
