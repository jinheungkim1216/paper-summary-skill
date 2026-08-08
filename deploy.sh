#!/usr/bin/env bash
# Deploy the dev version of the paper-summary skill to the global skills dir.
# Usage: ./deploy.sh
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="$HOME/.claude/skills/paper-summary"

rm -rf "$DEST_DIR"
mkdir -p "$DEST_DIR/scripts" "$DEST_DIR/domains"
cp "$SRC_DIR/SKILL.md"       "$DEST_DIR/SKILL.md"
cp "$SRC_DIR/scripts/"*      "$DEST_DIR/scripts/"
cp "$SRC_DIR/domains/"*.md   "$DEST_DIR/domains/"
chmod +x "$DEST_DIR/scripts/render_pdf.sh" "$DEST_DIR/scripts/setup.sh"

echo "Deployed paper-summary skill -> $DEST_DIR"
find "$DEST_DIR" -type f | sort
