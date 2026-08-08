#!/usr/bin/env bash
# Render a paper-summary Markdown file to PDF via Typst.
#   md --(pandoc)--> typ --(typst compile)--> pdf
# No browser / MathJax needed. Math is Typst-native; Korean uses a CJK fallback.
#
# Usage: render_pdf.sh <summary.md> [output.pdf]
set -euo pipefail

INPUT="$1"
OUT="${2:-${INPUT%.md}.pdf}"
DIR="$(cd "$(dirname "$INPUT")" && pwd)"
TYP="${INPUT%.md}.typ"
FRAG="$(mktemp "${TMPDIR:-/tmp}/ps_frag.XXXXXX.typ")"

command -v pandoc >/dev/null || { echo "pandoc not found" >&2; exit 1; }
command -v typst  >/dev/null || { echo "typst not found"  >&2; exit 1; }

# 0) Normalize deprecated TeX font commands that pandoc's Typst math reader
#    rejects (it accepts \mathrm{} but NOT the old {\rm ...} form). MathJax was
#    lenient about these; Typst is not. Handles the common flat cases.
PRE="$(mktemp "${TMPDIR:-/tmp}/ps_pre.XXXXXX.md")"
perl -pe '
  s/\\rm\s+([A-Za-z0-9]+)/\\mathrm{$1}/g;
  s/\\bf\s+([A-Za-z0-9]+)/\\mathbf{$1}/g;
  s/\\it\s+([A-Za-z0-9]+)/\\mathit{$1}/g;
  s/\\cal\s+([A-Za-z0-9]+)/\\mathcal{$1}/g;
  s/\\sf\s+([A-Za-z0-9]+)/\\mathsf{$1}/g;
  s/\\tt\s+([A-Za-z0-9]+)/\\mathtt{$1}/g;
' "$INPUT" > "$PRE"

# 1) Markdown -> Typst body fragment (no standalone template; we supply our own).
#    tex_math_dollars keeps $...$/$$...$$; the body's first '# ' becomes the title heading.
pandoc "$PRE" -f markdown-yaml_metadata_block+tex_math_dollars -t typst -o "$FRAG"
rm -f "$PRE"

# 2) Pick a Hangul font that is actually installed. Naming absent families makes
#    Typst warn on every render, so filter the candidates against `typst fonts`.
#    Order = preference: macOS system font, then the usual Linux packages.
HANGUL_CANDIDATES=("Apple SD Gothic Neo" "Noto Sans CJK KR" "Noto Serif CJK KR" "NanumGothic" "AppleGothic")
INSTALLED="$(typst fonts 2>/dev/null || true)"
HANGUL_FONT=""
for f in "${HANGUL_CANDIDATES[@]}"; do
  if grep -qxF "$f" <<<"$INSTALLED"; then HANGUL_FONT="$f"; break; fi
done
if [ -z "$HANGUL_FONT" ]; then
  echo "render_pdf.sh: no Hangul font found — Korean text may render as boxes." >&2
  echo "  install one:  brew install --cask font-nanum-gothic   |   apt install fonts-noto-cjk" >&2
  FONT_LIST='"Libertinus Serif"'
else
  FONT_LIST="\"Libertinus Serif\", \"$HANGUL_FONT\""
fi

# 3) Prepend a preamble: A4 page, Latin serif (Libertinus, bundled with Typst)
#    with the resolved Hangul fallback, justified paragraphs.
{
  cat <<TYPEOF
#set page(paper: "a4", margin: (x: 2cm, top: 2cm, bottom: 2.2cm), numbering: "1")
#set text(font: ($FONT_LIST), size: 10pt, lang: "ko")
TYPEOF
  cat <<'TYPEOF'
#set par(justify: true, leading: 0.7em)
#show heading: set block(above: 1.1em, below: 0.6em)
#show heading.where(level: 1): set text(size: 1.5em)
#show heading.where(level: 2): set text(size: 1.2em)
#set table(stroke: 0.5pt + gray)
TYPEOF
  cat "$FRAG"
} > "$TYP"
rm -f "$FRAG"

# 4) Typst -> PDF. --root = the md's dir so relative figure paths (figures/...) resolve.
typst compile --root "$DIR" "$TYP" "$OUT"

echo "PDF: $OUT ($(du -h "$OUT" | cut -f1))"
