#!/usr/bin/env bash
# Deploy the dev version of the paper-summary skill to the global skills dir.
#
# Usage: ./deploy.sh
#        DEST_DIR=/tmp/elsewhere ./deploy.sh   # override the target (tests use this)
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="${DEST_DIR:-$HOME/.claude/skills/paper-summary}"
DEST_PARENT="$(dirname "$DEST_DIR")"

# Assemble a complete copy first, then swap it in. SKILL.md alone is enough for
# Claude to register the skill, so a half-finished install is worse than no
# install: the skill gets offered, then fails partway through on a missing
# domain guide. Staging next to the target keeps the swap a same-filesystem
# rename.
mkdir -p "$DEST_PARENT"
STAGE="$(mktemp -d "$DEST_PARENT/.paper-summary-deploy.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/scripts" "$STAGE/domains"
cp "$SRC_DIR/SKILL.md" "$STAGE/SKILL.md"
# -type f skips __pycache__ and anything else a test run leaves in scripts/.
find "$SRC_DIR/scripts" -maxdepth 1 -type f -exec cp {} "$STAGE/scripts/" \;
cp "$SRC_DIR/domains/"*.md "$STAGE/domains/"
chmod +x "$STAGE/scripts/render_pdf.sh" "$STAGE/scripts/setup.sh"
chmod 755 "$STAGE"

for required in SKILL.md scripts/ingest.py scripts/verify.py \
                scripts/render_pdf.sh scripts/setup.sh domains/general.md; do
  [ -f "$STAGE/$required" ] || {
    echo "deploy: refusing to install — staging is missing $required" >&2
    exit 1
  }
done

rm -rf "$DEST_DIR"
mv "$STAGE" "$DEST_DIR"
trap - EXIT

echo "Deployed paper-summary skill -> $DEST_DIR"
find "$DEST_DIR" -type f | sort
