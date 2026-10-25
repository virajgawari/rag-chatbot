"""Filename sanitization and file-content validation."""
import io
import re
import zipfile
from pathlib import Path
from typing import Tuple

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"


def sanitize_filename(filename: str) -> str:
    """Reduce a client filename to a safe basename."""
    clean = Path(filename).name
    clean = re.sub(r"[^\w\s\.-]", "_", clean)
    clean = clean.strip()
    if clean in {"", ".", ".."} or not clean.strip(". "):
        return "document"
    return clean


def _looks_like_text(sample: bytes) -> bool:
    if b"\x00" in sample:
        return False
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = sample.decode("latin-1")
        except Exception:  # noqa: BLE001 - defensive
            return False
    if not text:
        return True
    control = sum(1 for char in text if ord(char) < 9 or 13 < ord(char) < 32)
    return control / max(1, len(text)) < 0.02


def validate_file_content(data: bytes, filename: str) -> Tuple[bool, str]:
    """Validate extension against actual content, not just the declared MIME type."""
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        if PDF_MAGIC not in data[:1024]:
            return False, "File content is not a valid PDF document."
        return True, ""

    if suffix == ".docx":
        if not data.startswith(ZIP_MAGIC):
            return False, "File content is not a valid DOCX document."
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" not in archive.namelist():
                    return False, "File content is not a valid DOCX document."
        except zipfile.BadZipFile:
            return False, "File content is not a valid DOCX document."
        return True, ""

    if suffix == ".txt":
        if not _looks_like_text(data[:4096]):
            return False, "File content is not plain text."
        return True, ""

    return False, f"Unsupported file extension: {suffix or 'unknown'}"
