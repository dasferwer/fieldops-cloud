#!/bin/sh
set -eu

alembic upgrade head
python -m fieldops.seed
exec "$@"

