#!/usr/bin/env python3
"""Generate one summary-skeleton .md per PDF in a folder, plus a full-text
.txt extraction for each — so an LLM agent only has to READ the .txt and
FILL IN the "Core insights" section, instead of writing PDF-parsing code
from scratch every time (the actual failure mode this script exists to
avoid: repeated syntax errors in ad-hoc extraction code, and summaries
that never get past a placeholder header as a result).

Usage:
    python3 pdf_summary_skeleton.py <papers_dir> <summaries_dir>

Requires `pdftotext` and `pdfinfo` (poppler-utils) on PATH. Uses only
subprocess + stdlib — no PDF-parsing library needed, deliberately: a
single, correct implementation here beats re-deriving PyMuPDF calls
inline in every future execute_code turn.
"""
import re
import subprocess
import sys
from pathlib import Path


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def pdf_info(pdf_path):
    result = run(["pdfinfo", str(pdf_path)])
    info = {}
    for line in result.stdout.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            info[key.strip()] = value.strip()
    return info


def pdf_text(pdf_path):
    result = run(["pdftotext", "-layout", str(pdf_path), "-"])
    return result.stdout


def slugify(name):
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    papers_dir = Path(sys.argv[1])
    summaries_dir = Path(sys.argv[2])
    summaries_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(papers_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {papers_dir}")
        return

    for pdf_path in pdfs:
        stem = slugify(pdf_path.stem)
        info = pdf_info(pdf_path)
        text = pdf_text(pdf_path)

        title = info.get("Title") or pdf_path.stem
        authors = info.get("Author", "(unknown)")
        pages = info.get("Pages", "?")

        txt_path = summaries_dir / f"{stem}.txt"
        txt_path.write_text(text, encoding="utf-8")

        md_path = summaries_dir / f"{stem}.md"
        is_html = text.strip()[:15].lower().startswith(("<!doctype", "<html"))
        if len(text.strip()) < 200 or is_html:
            reason = (
                "the file looks like an HTML page saved with a .pdf "
                "extension (a failed download), not a real PDF"
                if is_html else
                "pdftotext extracted almost no text — possibly a scanned/"
                "image-only PDF (would need OCR) or a corrupt/truncated file"
            )
            md_path.write_text(
                f"# {pdf_path.stem}\n\n"
                f"**Source:** `{pdf_path}`\n\n"
                f"## EXTRACTION FAILED\n\n"
                f"_{reason}. Re-download or otherwise fix the source file "
                f"before writing a summary — do not fabricate insights for "
                f"a paper whose text couldn't be read._\n",
                encoding="utf-8",
            )
            print(f"{pdf_path.name} -> EXTRACTION FAILED ({reason})")
            continue

        md_path.write_text(
            f"# {title}\n\n"
            f"**Authors:** {authors}\n\n"
            f"**Source:** `{pdf_path.relative_to(papers_dir.parent) if papers_dir.parent in pdf_path.parents else pdf_path}` "
            f"({pages} pages)\n\n"
            f"**Full extracted text:** [`{stem}.txt`]({stem}.txt)\n\n"
            "## Core insights\n\n"
            "_TODO: read the extracted text above and write the paper's key "
            "findings, method, and significance here. Do not leave this "
            "placeholder — the extraction is done; only the summary itself "
            "is left._\n",
            encoding="utf-8",
        )
        print(f"{pdf_path.name} -> {md_path.name} + {txt_path.name} "
              f"({len(text)} chars extracted, {pages} pages)")


if __name__ == "__main__":
    main()
