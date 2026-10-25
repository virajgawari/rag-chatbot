"""Page-aware text extraction for uploaded documents.

This module is intentionally dependency-light and has no access to request
state, storage, or credentials. `DocumentService` writes the uploaded bytes to a
temporary file and runs `extract_raw_pages` in a worker thread.
"""
from pathlib import Path
from typing import List


def extract_raw_pages(file_path: Path) -> List[tuple[int, str]]:
    """Extract non-empty text pages from a PDF, DOCX, or TXT file.

    Returns a list of ``(page_number, text)`` tuples with 1-based page numbers.
    DOCX and TXT files are represented as page 1 because the format does not
    expose stable pages without a renderer.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    pages: List[tuple[int, str]] = []

    if suffix == ".pdf":
        import pypdf

        reader = pypdf.PdfReader(str(path))
        for page_index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append((page_index + 1, text))

    elif suffix == ".docx":
        import docx2txt

        text = docx2txt.process(str(path)) or ""
        if text.strip():
            pages.append((1, text))

    elif suffix == ".txt":
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1", errors="replace")
        if text.strip():
            pages.append((1, text))

    else:
        raise ValueError(
            f"Unsupported file format: {suffix}. Supported formats: .pdf, .docx, .txt"
        )

    return pages
