#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx",
#     "pymupdf",
#     "trafilatura",
#     "feedparser",
# ]
# ///
"""Ingestion engine for the paper-summary skill.

Resolves a paper from one of three input kinds and produces a self-contained
work folder plus a manifest.json that the skill reads to write the summary.

  1. arXiv ID / URL  -> download the e-print SOURCE (.tex + figures). Highest
     fidelity: real equations and original figures.
  2. Local PDF       -> if an arXiv ID is detectable in the metadata / first
     page, fall through to (1); otherwise extract text + images with PyMuPDF.
  3. Web URL         -> extract the main article text with trafilatura.

Usage:
    uv run ingest.py <input> [--base-dir DIR] [--domain DOMAIN]

<input> is an arXiv id (2301.12345 / hep-ph/9901001), an arXiv/web URL, or a
path to a local .pdf file. The resolved work folder is created under --base-dir
(default: current working directory). On success the script prints the absolute
path of manifest.json on the last line of stdout.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import httpx

UA = ("paper-summary-skill/1.0 "
      "(+https://github.com/jinheungkim1216/paper-summary-skill; "
      "mailto:jinheung.kim1216@gmail.com)")
ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_EPRINT = "https://arxiv.org/e-print/{id}"

# arXiv id patterns: new style (2301.12345 / 2301.12345v3) and old style
# (hep-ph/9901001, math.AG/0309001 ...).
ARXIV_NEW = re.compile(r"\b(\d{4}\.\d{4,5})(v\d+)?\b")
ARXIV_OLD = re.compile(r"\b([a-z][a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")

RASTER_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def slugify(text: str, fallback: str = "paper") -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s\-]", "", text)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return (text[:80] or fallback).strip("-") or fallback


def domain_for_category(cat: str | None) -> str:
    """Map an arXiv primary category to a summary domain guide."""
    if not cat:
        return "general"
    c = cat.lower()
    if c.startswith("hep-"):
        return "hep"
    ai_cats = {
        "cs.lg", "cs.ai", "cs.cl", "cs.cv", "cs.ne", "cs.ir", "cs.ro", "stat.ml",
    }
    if c in ai_cats:
        return "ai"
    phys_prefixes = (
        "astro-ph", "cond-mat", "gr-qc", "quant-ph", "nucl-", "physics.",
        "math-ph", "nlin",
    )
    if any(c.startswith(p) for p in phys_prefixes):
        return "physics"
    return "general"


def detect_input(arg: str) -> tuple[str, str]:
    """Return (kind, value) where kind in {'arxiv', 'pdf', 'url'}."""
    p = Path(arg).expanduser()
    if p.is_file() and p.suffix.lower() == ".pdf":
        return "pdf", str(p.resolve())
    if "arxiv.org" in arg:
        aid = extract_arxiv_id(arg)
        if aid:
            return "arxiv", aid
    if arg.startswith(("http://", "https://")):
        return "url", arg
    if extract_arxiv_id(arg):
        return "arxiv", extract_arxiv_id(arg)  # bare id
    # last resort: treat as a path that may not exist yet
    return "url", arg


def extract_arxiv_id(text: str) -> str | None:
    m = ARXIV_NEW.search(text)
    if m:
        return m.group(1) + (m.group(2) or "")
    m = ARXIV_OLD.search(text)
    if m:
        return m.group(1) + (m.group(2) or "")
    return None


# --------------------------------------------------------------------------- #
# arXiv metadata + source
# --------------------------------------------------------------------------- #
def fetch_arxiv_metadata(arxiv_id: str, warnings: list[str]) -> dict:
    """Query the arXiv API for title/authors/year/primary_category/abstract."""
    import feedparser

    bare = re.sub(r"v\d+$", "", arxiv_id)
    meta: dict = {
        "title": None, "authors": [], "year": None,
        "primary_category": None, "abstract": None,
    }
    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
            r = c.get(ARXIV_API, params={"id_list": bare, "max_results": 1})
            r.raise_for_status()
        feed = feedparser.parse(r.text)
        if not feed.entries:
            warnings.append(f"arXiv API returned no entry for {bare}")
            return meta
        e = feed.entries[0]
        meta["title"] = re.sub(r"\s+", " ", e.get("title", "")).strip() or None
        meta["authors"] = [a.get("name") for a in e.get("authors", []) if a.get("name")]
        published = e.get("published", "")
        meta["year"] = published[:4] if published[:4].isdigit() else None
        tag = e.get("arxiv_primary_category") or (e.get("tags") or [{}])[0]
        meta["primary_category"] = tag.get("term") if tag else None
        meta["abstract"] = re.sub(r"\s+", " ", e.get("summary", "")).strip() or None
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"arXiv metadata fetch failed: {exc}")
    return meta


def download_arxiv_source(arxiv_id: str, warnings: list[str]) -> tuple[str, bytes] | None:
    """Download the e-print blob. Returns (kind, bytes) where kind is one of
    'tar', 'gz-tex', 'pdf', or None on failure."""
    url = ARXIV_EPRINT.format(id=arxiv_id)
    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=120, follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
            data = r.content
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"e-print download failed: {exc}")
        return None

    if data[:5] == b"%PDF-":
        return "pdf", data
    # tarfile transparently handles gzip/bzip/xz tarballs.
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*"):
            return "tar", data
    except tarfile.TarError:
        pass
    # single gzipped file (usually one .tex)
    try:
        return "gz-tex", gzip.decompress(data)
    except OSError:
        pass
    warnings.append("e-print blob was neither tar, gzip, nor PDF")
    return None


def safe_extract_tar(data: bytes, dest: Path) -> None:
    """Extract a tarball, refusing path-traversal members."""
    dest = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        for m in tar.getmembers():
            target = (dest / m.name).resolve()
            if not str(target).startswith(str(dest)):
                continue  # skip traversal attempt
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif m.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, open(target, "wb") as out:
                    if src:
                        shutil.copyfileobj(src, out)


def find_main_tex(source_dir: Path) -> Path | None:
    """Pick the main .tex: must contain \\documentclass + \\begin{document};
    prefer one that also has \\title; tie-break by size."""
    tex_files = list(source_dir.rglob("*.tex"))
    if not tex_files:
        return None
    scored: list[tuple[int, int, Path]] = []
    for t in tex_files:
        try:
            txt = t.read_text(errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        score = 0
        if "\\documentclass" in txt:
            score += 4
        if "\\begin{document}" in txt:
            score += 4
        if "\\title" in txt or "\\maketitle" in txt:
            score += 2
        scored.append((score, len(txt), t))
    if not scored:
        return None
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return scored[0][2]


# --------------------------------------------------------------------------- #
# figures
# --------------------------------------------------------------------------- #
def unique_figure_target(fig_dir: Path, source_dir: Path, src: Path, ext: str,
                         used: set[str]) -> Path:
    """Pick a collision-free target name for a figure copied out of source_dir.

    Papers routinely ship figs/plot.pdf alongside plots/plot.pdf; naming targets
    by slugified stem alone made the second silently overwrite the first. Fall
    back to a parent-directory-qualified name, then to a numeric suffix.
    """
    stem = slugify(src.stem, fallback="fig")
    candidates = [stem]
    rel_parent = src.parent.relative_to(source_dir)
    if rel_parent.parts:
        candidates.append(slugify("-".join(rel_parent.parts + (stem,)), fallback="fig"))
    name = next((c for c in candidates if f"{c}{ext}" not in used), None)
    if name is None:
        i = 2
        while f"{stem}-{i}{ext}" in used:
            i += 1
        name = f"{stem}-{i}"
    used.add(f"{name}{ext}")
    return fig_dir / f"{name}{ext}"


def eps_to_png(src: Path, target: Path) -> bool:
    """Rasterize an EPS/PS figure with ghostscript. False if gs is unavailable."""
    gs = shutil.which("gs")
    if not gs:
        return False
    subprocess.run(
        [gs, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dEPSCrop", "-sDEVICE=png16m",
         "-r150", f"-sOutputFile={target}", str(src)],
        check=True, capture_output=True,
    )
    return True


def collect_bibliography(source_dir: Path, work_dir: Path) -> list[str]:
    """Relative paths of .bbl/.bib files.

    Without these the model sees \\cite{key} in the body with no way to resolve
    what it cites, which makes a grounded related-work section impossible.
    """
    return sorted(
        str(p.relative_to(work_dir))
        for p in source_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".bbl", ".bib"}
    )


def content_chars(work_dir: Path, rel_paths: list[str]) -> int:
    """Total character count of the given work-folder-relative files.

    Lets the skill decide whether a paper fits one context or needs subagent
    fan-out, instead of guessing.
    """
    total = 0
    for rel in rel_paths:
        p = work_dir / rel
        try:
            total += len(p.read_text(errors="ignore"))
        except OSError:
            continue
    return total


def collect_figures(source_dir: Path, fig_dir: Path, warnings: list[str],
                    cap: int = 40) -> tuple[list[str], dict[str, str]]:
    """Copy raster figures and convert PDF/EPS figures to PNG.

    Returns (figure paths relative to the work folder, a map from the path as
    written in \\includegraphics to the collected file).
    """
    import pymupdf as fitz  # PyMuPDF

    fig_dir.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    fig_map: dict[str, str] = {}
    used: set[str] = set()

    def record(src: Path, target: Path) -> None:
        rel = f"figures/{target.name}"
        out.append(rel)
        # \includegraphics usually omits the extension, but not always.
        key = src.relative_to(source_dir).as_posix()
        fig_map[key] = rel
        fig_map[key.rsplit(".", 1)[0]] = rel

    candidates = sorted(
        p for p in source_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in RASTER_EXT | {".pdf", ".eps", ".ps"}
    )
    skipped_eps = 0
    for p in candidates:
        if len(out) >= cap:
            warnings.append(f"figure cap ({cap}) reached; remaining figures not converted")
            break
        ext = p.suffix.lower()
        try:
            if ext in RASTER_EXT:
                target = unique_figure_target(fig_dir, source_dir, p, ext, used)
                shutil.copyfile(p, target)
                record(p, target)
            elif ext == ".pdf":
                doc = fitz.open(p)
                if doc.page_count == 0:
                    doc.close()
                    continue
                pix = doc.load_page(0).get_pixmap(dpi=150)
                target = unique_figure_target(fig_dir, source_dir, p, ".png", used)
                pix.save(target)
                doc.close()
                record(p, target)
            else:  # eps / ps
                target = unique_figure_target(fig_dir, source_dir, p, ".png", used)
                if eps_to_png(p, target):
                    record(p, target)
                else:
                    used.discard(target.name)
                    skipped_eps += 1
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"figure conversion failed for {p.name}: {exc}")
    if skipped_eps:
        warnings.append(
            f"{skipped_eps} EPS/PS figure(s) skipped (ghostscript not found; "
            f"install it with: brew install ghostscript)"
        )
    return out, fig_map


# --------------------------------------------------------------------------- #
# PDF + URL extraction
# --------------------------------------------------------------------------- #
def pdf_first_pages_text(pdf_path: Path, n: int = 2) -> str:
    import pymupdf as fitz

    try:
        doc = fitz.open(pdf_path)
        text = "\n".join(doc.load_page(i).get_text() for i in range(min(n, doc.page_count)))
        doc.close()
        return text
    except Exception:  # noqa: BLE001
        return ""


def pdf_metadata_title(pdf_path: Path) -> str | None:
    import pymupdf as fitz

    try:
        doc = fitz.open(pdf_path)
        title = (doc.metadata or {}).get("title")
        doc.close()
        return title.strip() if title and title.strip() else None
    except Exception:  # noqa: BLE001
        return None


def extract_pdf(pdf_path: Path, work_dir: Path, warnings: list[str]) -> tuple[str, list[str]]:
    """Extract full text (.txt) and embedded raster images from a local PDF.
    Returns (relative text path, figure rel-paths)."""
    import pymupdf as fitz

    src_dir = work_dir / "source"
    src_dir.mkdir(parents=True, exist_ok=True)
    if pdf_path.resolve() != (src_dir / pdf_path.name).resolve():
        shutil.copyfile(pdf_path, src_dir / pdf_path.name)

    text_parts: list[str] = []
    figs: list[str] = []
    fig_dir = work_dir / "figures"
    try:
        doc = fitz.open(pdf_path)
        for i in range(doc.page_count):
            page = doc.load_page(i)
            text_parts.append(f"\n\n===== page {i + 1} =====\n")
            text_parts.append(page.get_text())
        # extract embedded images (raster) up to a cap
        seen: set[int] = set()
        for i in range(doc.page_count):
            for img in doc.load_page(i).get_images(full=True):
                xref = img[0]
                if xref in seen or len(figs) >= 30:
                    continue
                seen.add(xref)
                try:
                    base = doc.extract_image(xref)
                    ext = base.get("ext", "png")
                    if base["image"] and base.get("width", 0) >= 80:
                        fig_dir.mkdir(parents=True, exist_ok=True)
                        name = f"p{i + 1}-x{xref}.{ext}"
                        (fig_dir / name).write_bytes(base["image"])
                        figs.append(f"figures/{name}")
                except Exception:  # noqa: BLE001
                    continue
        doc.close()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"PDF extraction error: {exc}")

    txt_path = work_dir / "extracted.txt"
    txt_path.write_text("".join(text_parts), errors="ignore")
    return "extracted.txt", figs


def is_pdf_payload(head: bytes, content_type: str | None) -> bool:
    """Whether a fetched response is a PDF rather than a web page.

    Magic bytes win: OpenReview, ACL Anthology and journal hosts often serve
    PDFs as application/octet-stream.
    """
    if head[:5] == b"%PDF-":
        return True
    if content_type and content_type.split(";")[0].strip().lower() == "application/pdf":
        return True
    return False


def download_url(url: str, warnings: list[str]) -> tuple[bytes, str | None] | None:
    """Fetch a URL once, returning (body, content-type)."""
    try:
        with httpx.Client(headers={"User-Agent": UA}, timeout=120, follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
            return r.content, r.headers.get("content-type")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"URL fetch failed: {exc}")
        return None


def extract_url(url: str, work_dir: Path, warnings: list[str],
                raw_html: bytes | None = None) -> tuple[str, str | None]:
    """Extract the main article text from a web page. Returns
    (relative text path, extracted title). Reuses raw_html when the caller has
    already fetched the body, so the page is not downloaded twice."""
    import trafilatura

    title = None
    text = None
    try:
        downloaded = (raw_html.decode("utf-8", errors="ignore")
                      if raw_html is not None else trafilatura.fetch_url(url))
        if downloaded:
            text = trafilatura.extract(
                downloaded, include_comments=False, include_tables=True,
                favor_recall=True,
            )
            md = trafilatura.extract_metadata(downloaded)
            if md and md.title:
                title = md.title
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"URL extraction error: {exc}")
    if not text:
        warnings.append("trafilatura returned no text; falling back to raw fetch")
        try:
            with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as c:
                text = c.get(url).text
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"raw fetch failed: {exc}")
            text = ""
    (work_dir / "extracted.txt").write_text(text or "", errors="ignore")
    return "extracted.txt", title


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def make_work_dir(base_dir: Path, title: str | None, arxiv_id: str | None,
                  input_arg: str) -> Path:
    if title:
        name = slugify(title)
    elif arxiv_id:
        name = "arxiv-" + slugify(arxiv_id)
    else:
        name = slugify(Path(input_arg).stem)
    work = base_dir / name
    suffix = 2
    while work.exists() and any(work.iterdir()):
        work = base_dir / f"{name}-{suffix}"
        suffix += 1
    work.mkdir(parents=True, exist_ok=True)
    return work


def ingest_arxiv(arxiv_id: str, base_dir: Path, warnings: list[str],
                 manifest: dict) -> dict:
    meta = fetch_arxiv_metadata(arxiv_id, warnings)
    manifest.update(meta)
    manifest["arxiv_id"] = arxiv_id

    work_dir = make_work_dir(base_dir, meta.get("title"), arxiv_id, arxiv_id)
    manifest["work_dir"] = str(work_dir)
    src_dir = work_dir / "source"
    src_dir.mkdir(parents=True, exist_ok=True)

    blob = download_arxiv_source(arxiv_id, warnings)
    if blob is None:
        # No source: fall back to downloading the PDF.
        warnings.append("falling back to arXiv PDF (no usable source)")
        pdf_path = src_dir / f"{slugify(arxiv_id)}.pdf"
        try:
            with httpx.Client(headers={"User-Agent": UA}, timeout=120, follow_redirects=True) as c:
                r = c.get(f"https://arxiv.org/pdf/{arxiv_id}")
                r.raise_for_status()
                pdf_path.write_bytes(r.content)
            manifest["ingest_method"] = "arxiv-pdf"
            txt, figs = extract_pdf(pdf_path, work_dir, warnings)
            manifest["main_content"] = txt
            manifest["extracted_text"] = txt
            manifest["figures"] = figs
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"arXiv PDF fallback failed: {exc}")
        return manifest

    kind, payload = blob
    if kind == "pdf":
        warnings.append("e-print was a PDF (no LaTeX source submitted)")
        pdf_path = src_dir / f"{slugify(arxiv_id)}.pdf"
        pdf_path.write_bytes(payload)
        manifest["ingest_method"] = "arxiv-pdf"
        txt, figs = extract_pdf(pdf_path, work_dir, warnings)
        manifest["main_content"] = txt
        manifest["extracted_text"] = txt
        manifest["figures"] = figs
        return manifest

    if kind == "gz-tex":
        main = src_dir / "main.tex"
        main.write_bytes(payload)
    else:  # tar
        safe_extract_tar(payload, src_dir)

    manifest["ingest_method"] = "arxiv-source"
    main_tex = find_main_tex(src_dir)
    if main_tex:
        manifest["main_content"] = str(main_tex.relative_to(work_dir))
    else:
        warnings.append("no main .tex found in source")
    manifest["all_tex"] = [str(p.relative_to(work_dir)) for p in src_dir.rglob("*.tex")]
    manifest["bibliography"] = collect_bibliography(src_dir, work_dir)
    manifest["figures"], manifest["figures_map"] = collect_figures(
        src_dir, work_dir / "figures", warnings
    )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="paper-summary ingestion engine")
    ap.add_argument("input", help="arXiv id/URL, local .pdf path, or web URL")
    ap.add_argument("--base-dir", default=os.getcwd(),
                    help="directory under which the work folder is created")
    ap.add_argument("--domain", default=None,
                    help="override the auto-detected domain (hep|ai|physics|general)")
    args = ap.parse_args()

    base_dir = Path(args.base_dir).expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    kind, value = detect_input(args.input)
    log(f"[ingest] input detected as: {kind} -> {value}")

    manifest: dict = {
        "input": args.input,
        "input_type": kind,
        "arxiv_id": None,
        "title": None,
        "authors": [],
        "year": None,
        "primary_category": None,
        "abstract": None,
        "ingest_method": None,
        "work_dir": None,
        "main_content": None,
        "extracted_text": None,
        "all_tex": [],
        "bibliography": [],
        "figures": [],
        "figures_map": {},
        "content_chars": 0,
        "warnings": warnings,
    }

    # A PDF may itself be an arXiv paper: prefer the source if so.
    if kind == "pdf":
        ingest_pdf_file(Path(value), base_dir, warnings, manifest, "pdf->arxiv")
    elif kind == "arxiv":
        ingest_arxiv(value, base_dir, warnings, manifest)
    else:  # url — may serve HTML or a PDF
        payload = download_url(value, warnings)
        body, ctype = payload if payload else (b"", None)
        if payload and is_pdf_payload(body, ctype):
            log("[ingest] URL served a PDF; using the PDF extractor")
            manifest["input_type"] = "url->pdf"
            with tempfile.TemporaryDirectory() as td:
                tmp_pdf = Path(td) / f"{slugify(Path(value).stem, fallback='paper')}.pdf"
                tmp_pdf.write_bytes(body)
                ingest_pdf_file(tmp_pdf, base_dir, warnings, manifest, "url->arxiv")
            if manifest.get("ingest_method") == "local-pdf":
                manifest["ingest_method"] = "url-pdf"
        else:
            work_dir = make_work_dir(base_dir, None, None, value or "web-paper")
            manifest["work_dir"] = str(work_dir)
            manifest["ingest_method"] = "url"
            txt, title = extract_url(value, work_dir, warnings, raw_html=body or None)
            manifest["main_content"] = txt
            manifest["extracted_text"] = txt
            if title:
                manifest["title"] = title

    # Domain resolution (override wins).
    manifest["domain"] = args.domain or domain_for_category(manifest.get("primary_category"))
    manifest["domain_subcategory"] = manifest.get("primary_category")

    if not manifest.get("work_dir"):
        log("[ingest] FATAL: no work dir produced")
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 1

    # Size of whatever the skill will actually read, so it can decide between
    # reading inline and fanning out to subagents.
    work = Path(manifest["work_dir"])
    read_targets = manifest["all_tex"] or (
        [manifest["extracted_text"]] if manifest.get("extracted_text") else []
    )
    manifest["content_chars"] = content_chars(work, read_targets)

    manifest_path = Path(manifest["work_dir"]) / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    log(f"[ingest] done. method={manifest['ingest_method']} domain={manifest['domain']} "
        f"figures={len(manifest['figures'])} warnings={len(warnings)}")
    print(str(manifest_path))
    return 0


def ingest_pdf_file(pdf_path: Path, base_dir: Path, warnings: list[str],
                    manifest: dict, arxiv_tag: str) -> None:
    """Ingest a PDF on disk, preferring the arXiv source when the PDF reveals an
    arXiv id. Shared by the local-file and remote-URL entry points."""
    probe = (pdf_metadata_title(pdf_path) or "") + "\n" + pdf_first_pages_text(pdf_path)
    aid = extract_arxiv_id(probe)
    if aid:
        log(f"[ingest] arXiv id {aid} found in PDF; fetching source instead")
        manifest["input_type"] = arxiv_tag
        ingest_arxiv(aid, base_dir, warnings, manifest)
        if manifest.get("main_content"):
            return
        warnings.append("arXiv source via PDF failed; using the PDF text")
    _ingest_local_pdf(pdf_path, base_dir, warnings, manifest)


def _ingest_local_pdf(pdf_path: Path, base_dir: Path, warnings: list[str],
                      manifest: dict) -> None:
    title = pdf_metadata_title(pdf_path)
    work_dir = make_work_dir(base_dir, title, None, str(pdf_path))
    manifest["work_dir"] = str(work_dir)
    manifest["ingest_method"] = "local-pdf"
    if title:
        manifest["title"] = title
    txt, figs = extract_pdf(pdf_path, work_dir, warnings)
    manifest["main_content"] = txt
    manifest["extracted_text"] = txt
    manifest["figures"] = figs


if __name__ == "__main__":
    sys.exit(main())
