"""Download primary-source PDFs and preserve selected pages for repeatable QA.

Run: uv run --with pypdf python scripts/prepare_public_documents.py
Excerpts retain original page contents and annotation objects, not synthetic text.
"""

import hashlib
import json
from pathlib import Path

import httpx
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1] / ".data" / "validation"
PACKAGES = [
    {
        "name": "alachua-addendum",
        "url": "https://www.cityofalachua.com/DocumentCenter/View/1321/Addendum-Number-1-w-Attachments?bidId=",
        "sha256": "4d69cfaaf238e139c62f3f6b7bfd3c418fd0435cd875704fd5c0e1970e03a7f2",
        "pages": [33, 40, 45, 47, 48, 59, 67],
    },
    {
        "name": "avta-board-package",
        "url": "https://www.avta.com/downloads/meetings/bod/2018/042418-agenda.pdf",
        "sha256": "a2f248fdef015e5dffef27bf24ce93cf35717c1cba8e6d45b984360b98b14086",
        "pages": [77, 78, 79, 80],
    },
]


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for package in PACKAGES:
        original = ROOT / (package["name"] + ".pdf")
        if not original.exists():
            with httpx.stream(
                "GET", package["url"], follow_redirects=True, timeout=90
            ) as response:
                response.raise_for_status()
                with original.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
        actual = hashlib.sha256(original.read_bytes()).hexdigest()
        if actual != package["sha256"]:
            raise ValueError(
                f"Source changed: {original.name}. Review it before updating the expected hash."
            )
        reader = PdfReader(original)
        writer = PdfWriter()
        for page in package["pages"]:
            writer.add_page(reader.pages[page - 1])
        writer.add_metadata(
            {
                "/Title": package["name"] + " — public-source excerpt",
                "/Subject": "Original PDF pages "
                + ", ".join(map(str, package["pages"]))
                + "; "
                + package["url"],
            }
        )
        excerpt = ROOT / (package["name"] + "-excerpt.pdf")
        writer.write(excerpt)
        manifest.append(
            {
                **package,
                "original_pages": len(reader.pages),
                "original_bytes": original.stat().st_size,
                "excerpt": excerpt.name,
                "page_map": {
                    str(i): page for i, page in enumerate(package["pages"], 1)
                },
            }
        )
        print(
            f"Prepared {excerpt.name}: {len(package['pages'])} of {len(reader.pages)} pages"
        )
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
