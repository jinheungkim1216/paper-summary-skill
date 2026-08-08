#!/usr/bin/env bash
# Run the paper-summary test suite.
# Deps are supplied by uv (ingest.py imports httpx at module scope and pymupdf
# lazily inside collect_figures), so no virtualenv setup is needed.
#
# Usage: ./tests/run.sh [pytest args...]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec uv run \
  --with pytest \
  --with httpx \
  --with pymupdf \
  pytest "$REPO/tests" "$@"
