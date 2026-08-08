"""Tests for the paper-summary ingestion engine.

Run with:  ./tests/run.sh
"""

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import ingest  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RENDER_SH = REPO / "scripts" / "render_pdf.sh"

# A minimal but valid EPS: a filled blue square on a 100x100 canvas.
MINIMAL_EPS = b"""%!PS-Adobe-3.0 EPSF-3.0
%%BoundingBox: 0 0 100 100
0 0 1 setrgbcolor
10 10 80 80 rectfill
showpage
%%EOF
"""

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Families that actually carry Hangul glyphs, across the platforms we target.
HANGUL_FONTS = {
    "Apple SD Gothic Neo", "AppleGothic",
    "Noto Sans CJK KR", "Noto Serif CJK KR",
    "NanumGothic", "NanumMyeongjo",
}


def test_same_stem_figures_in_different_dirs_do_not_collide(tmp_path):
    """Two figures sharing a basename must both survive with distinct names.

    Regression: collect_figures named targets by slugified stem alone, so
    a/plot.png and b/plot.png both mapped to figures/plot.png — the second
    silently overwrote the first and the manifest listed the path twice.
    """
    src = tmp_path / "source"
    (src / "a").mkdir(parents=True)
    (src / "b").mkdir(parents=True)
    (src / "a" / "plot.png").write_bytes(b"FIGURE-A")
    (src / "b" / "plot.png").write_bytes(b"FIGURE-B")

    figs, _ = ingest.collect_figures(src, tmp_path / "figures", [])

    assert len(figs) == 2, f"expected both figures kept, got {figs}"
    assert len(set(figs)) == 2, f"manifest entries must be distinct, got {figs}"
    contents = {(tmp_path / f).read_bytes() for f in figs}
    assert contents == {b"FIGURE-A", b"FIGURE-B"}, "a figure was overwritten"


def test_eps_figure_is_converted_to_png(tmp_path):
    """EPS/PS figures must be rasterized, not dropped.

    Old-style arXiv papers (hep-*) ship figures almost exclusively as EPS;
    dropping them left those summaries with no figures at all.
    """
    src = tmp_path / "source"
    src.mkdir(parents=True)
    (src / "diagram.eps").write_bytes(MINIMAL_EPS)

    figs, _ = ingest.collect_figures(src, tmp_path / "figures", [])

    assert figs == ["figures/diagram.png"], f"expected converted EPS, got {figs}"
    assert (tmp_path / "figures" / "diagram.png").read_bytes()[:8] == PNG_MAGIC


def test_bibliography_files_are_collected(tmp_path):
    """.bbl/.bib must reach the manifest so \\cite{} keys can be resolved.

    all_tex globs only *.tex, so the model saw \\cite{vaswani2017} with no way
    to learn what it refers to — while the template asks it to write a
    related-work section.
    """
    src = tmp_path / "source"
    (src / "sections").mkdir(parents=True)
    (src / "main.tex").write_text(r"\documentclass{article}")
    (src / "refs.bib").write_text("@article{a, title={A}}")
    (src / "sections" / "main.bbl").write_text(r"\bibitem{a} A. Author")

    bib = ingest.collect_bibliography(src, tmp_path)

    assert sorted(bib) == ["source/refs.bib", "source/sections/main.bbl"]


def test_figures_map_resolves_includegraphics_paths(tmp_path):
    """The manifest must translate \\includegraphics args to collected files.

    SKILL.md used to instruct the model to match Figures/ModalNet-21 to the
    slugified filename by hand; the script already knows the answer.
    """
    src = tmp_path / "source"
    (src / "Figures").mkdir(parents=True)
    (src / "Figures" / "ModalNet-21.png").write_bytes(b"FIG")

    figs, fig_map = ingest.collect_figures(src, tmp_path / "figures", [])

    assert figs == ["figures/modalnet-21.png"]
    # \includegraphics is usually written without the extension, but not always.
    assert fig_map["Figures/ModalNet-21"] == "figures/modalnet-21.png"
    assert fig_map["Figures/ModalNet-21.png"] == "figures/modalnet-21.png"


def test_content_chars_sums_every_source_file(tmp_path):
    """The manifest must say how big the paper is.

    SKILL.md tells the model to fan out to subagents for "long" papers but gave
    it no number to decide with.
    """
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "a.tex").write_text("x" * 100)
    (tmp_path / "source" / "b.tex").write_text("y" * 50)

    total = ingest.content_chars(tmp_path, ["source/a.tex", "source/b.tex"])

    assert total == 150


def test_content_chars_ignores_missing_files(tmp_path):
    """A stale manifest entry must not crash the size calculation."""
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "a.tex").write_text("x" * 10)

    assert ingest.content_chars(tmp_path, ["source/a.tex", "source/gone.tex"]) == 10


def test_pdf_payload_detected_by_magic_bytes():
    """A PDF served with a wrong or missing Content-Type must still be detected.

    Journal and OpenReview links routinely serve application/octet-stream.
    """
    assert ingest.is_pdf_payload(b"%PDF-1.7\n...", "application/octet-stream")
    assert ingest.is_pdf_payload(b"%PDF-1.4", None)


def test_pdf_payload_detected_by_content_type():
    """Content-Type alone is enough when the body isn't inspected yet."""
    assert ingest.is_pdf_payload(b"", "application/pdf")
    assert ingest.is_pdf_payload(b"", "application/pdf; charset=binary")


def test_html_payload_is_not_treated_as_pdf():
    """An ordinary article page must keep going through the HTML extractor."""
    assert not ingest.is_pdf_payload(b"<!doctype html><html>", "text/html")
    assert not ingest.is_pdf_payload(b"<html>", None)


def test_ingest_stdout_carries_only_the_manifest_path(tmp_path):
    """SKILL.md parses stdout for the manifest path; nothing else may go there.

    Regression: PyMuPDF printed its `fitz` deprecation notice to stdout, so the
    documented "last stdout line is manifest.json" contract was one library
    change away from breaking. Uses a local PDF so the test needs no network.
    """
    import fitz

    pdf = tmp_path / "paper.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "A paper with no arXiv identifier")
    doc.save(str(pdf))
    doc.close()

    out = subprocess.run(
        ["uv", "run", str(REPO / "scripts" / "ingest.py"), str(pdf),
         "--base-dir", str(tmp_path / "out")],
        check=True, capture_output=True, text=True,
    )

    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"stdout must hold only the manifest path, got {lines}"
    assert Path(lines[0]).name == "manifest.json", lines[0]


def test_remote_pdf_url_is_extracted_as_a_pdf(tmp_path):
    """A URL that serves a PDF must go to the PDF extractor, not trafilatura.

    Regression: OpenReview/ACL/journal PDF links fell into the `url` branch,
    where trafilatura parsed PDF bytes as HTML and produced garbage text.
    Served over localhost with a deliberately wrong Content-Type, which is what
    several real hosts do.
    """
    import fitz

    served = tmp_path / "served"
    served.mkdir()
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "GROUNDING TOKEN 8675309")
    doc.save(str(served / "paper.pdf"))
    doc.close()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(served), **kw)

        def guess_type(self, path):  # mimic hosts that mislabel PDFs
            return "application/octet-stream"

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/paper.pdf"

    try:
        out = subprocess.run(
            ["uv", "run", str(REPO / "scripts" / "ingest.py"), url,
             "--base-dir", str(tmp_path / "out")],
            check=True, capture_output=True, text=True,
        )
    finally:
        srv.shutdown()

    manifest = json.loads(Path(out.stdout.strip().splitlines()[-1]).read_text())

    assert manifest["ingest_method"] == "url-pdf", manifest["ingest_method"]
    text = (Path(manifest["work_dir"]) / manifest["extracted_text"]).read_text()
    assert "8675309" in text, "PDF body was not extracted"


def test_render_picks_an_installed_hangul_font_without_warnings(tmp_path):
    """Every font the preamble names must exist here, and one must cover Hangul.

    Regression: the list was hardcoded to ("Libertinus Serif", "Apple SD Gothic
    Neo"), so Korean rendered as tofu anywhere but macOS. Naively appending the
    Linux families instead makes Typst warn `unknown font family` on every
    render for the ones that aren't installed locally.
    """
    md = tmp_path / "summary.md"
    md.write_text("# 테스트 논문 — 요약\n\n한글 본문이 렌더링되어야 합니다.\n")

    proc = subprocess.run([str(RENDER_SH), str(md)], check=True,
                          capture_output=True, text=True)

    assert "unknown font family" not in proc.stderr, proc.stderr

    installed = set(subprocess.run(["typst", "fonts"], capture_output=True,
                                   text=True).stdout.splitlines())
    typ = (tmp_path / "summary.typ").read_text()
    font_line = next(ln for ln in typ.splitlines() if "set text(font" in ln)
    font_tuple = re.search(r"font:\s*\(([^)]*)\)", font_line).group(1)
    named = re.findall(r'"([^"]+)"', font_tuple)

    assert named, font_line
    assert set(named) <= installed, f"names a font that is not installed: {named}"
    assert set(named) & HANGUL_FONTS, f"no Hangul-capable font among {named}"


def test_render_degrades_gracefully_when_no_hangul_font_exists(tmp_path):
    """With no Hangul font installed, still produce a PDF and say why it's wrong.

    Simulated by shadowing `typst fonts` with a stub that reports an empty font
    list, while delegating `typst compile` to the real binary.
    """
    real_typst = shutil.which("typst")
    assert real_typst, "typst must be installed to run this test"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "typst"
    shim.write_text(
        f'#!/usr/bin/env bash\n'
        f'if [ "$1" = "fonts" ]; then exit 0; fi\n'
        f'exec {real_typst} "$@"\n'
    )
    shim.chmod(0o755)

    md = tmp_path / "summary.md"
    md.write_text("# 테스트 논문 — 요약\n\n한글 본문.\n")
    env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}"}

    proc = subprocess.run([str(RENDER_SH), str(md)], check=True,
                          capture_output=True, text=True, env=env)

    assert (tmp_path / "summary.pdf").exists(), "PDF must still be produced"
    assert "no Hangul font found" in proc.stderr, proc.stderr
    font_line = next(ln for ln in (tmp_path / "summary.typ").read_text().splitlines()
                     if "set text(font" in ln)
    assert '("Libertinus Serif")' in font_line, font_line


def test_render_script_offers_non_macos_hangul_candidates():
    """A box with only Noto/Nanum installed must still find a Hangul font.

    Guards the candidate pool itself, since a macOS test run would otherwise
    never exercise the Linux branch.
    """
    src = RENDER_SH.read_text()
    for family in ("Noto Sans CJK KR", "NanumGothic"):
        assert family in src, f"{family} missing from the font candidate list"
