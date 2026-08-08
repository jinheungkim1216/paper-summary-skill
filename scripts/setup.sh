#!/usr/bin/env bash
# Setup checker for the paper-summary skill.
# Verifies the external tools the skill needs and prints install hints for any
# that are missing. Exits non-zero if a REQUIRED tool is absent.
#
# Usage: ./setup.sh

req_missing=0

green()  { printf "  \033[32m✓\033[0m %-9s %s\n" "$1" "$2"; }
redx()   { printf "  \033[31m✗\033[0m %-9s %s\n" "$1" "$2"; }
warn()   { printf "  \033[33m!\033[0m %-9s %s\n" "$1" "$2"; }

# find a binary either on PATH or as an absolute/app path
have() { command -v "$1" >/dev/null 2>&1 || [ -x "$1" ]; }

echo "paper-summary — dependency check"
echo

# --- Required tools ---------------------------------------------------------
echo "Required:"

# uv — runs ingest.py (handles its own Python deps via PEP 723)
if have uv; then
  green "uv" "$(uv --version 2>/dev/null)"
else
  redx "uv" "MISSING — install: brew install uv   (or: curl -LsSf https://astral.sh/uv/install.sh | sh)"
  req_missing=$((req_missing+1))
fi

# pandoc — Markdown -> Typst (PDF step)
if have pandoc; then
  green "pandoc" "$(pandoc --version 2>/dev/null | head -1)"
else
  redx "pandoc" "MISSING — install: brew install pandoc   (or: mamba install -c conda-forge pandoc)"
  req_missing=$((req_missing+1))
fi

# typst — Typst -> PDF (PDF step)
if have typst; then
  green "typst" "$(typst --version 2>/dev/null)"
else
  redx "typst" "MISSING — install: brew install typst   (or: cargo install typst-cli)"
  req_missing=$((req_missing+1))
fi

echo

# --- Optional / recommended -------------------------------------------------
echo "Recommended:"

# ghostscript — rasterizes EPS/PS figures. Old-style arXiv papers (hep-*) ship
# figures almost exclusively as EPS; without gs those summaries get no figures.
if have gs; then
  green "gs" "$(gs --version 2>/dev/null) (EPS figures will be converted)"
else
  warn "gs" "MISSING — EPS/PS figures will be skipped. install: brew install ghostscript   (or: apt install ghostscript)"
fi

# A Korean-capable font so Typst renders Hangul (the renderer falls back to it).
KFONT=""
if command -v fc-list >/dev/null 2>&1; then
  KFONT="$(fc-list 2>/dev/null | grep -iE 'Apple SD Gothic|Noto Sans CJK KR|NanumGothic|Noto Serif CJK KR' | head -1)"
fi
if [ -n "$KFONT" ] || [ -f /System/Library/Fonts/AppleSDGothicNeo.ttc ]; then
  green "KR font" "found (Hangul will render)"
else
  warn "KR font" "no CJK-KR font found — Korean may show as boxes. macOS ships Apple SD Gothic Neo; on Linux: install Noto Sans CJK KR"
fi

echo
if [ "$req_missing" -eq 0 ]; then
  printf "\033[32mAll required tools present. Ready.\033[0m\n"
  exit 0
else
  printf "\033[31m%d required tool(s) missing — install the above, then re-run ./setup.sh\033[0m\n" "$req_missing"
  exit 1
fi
