"""Stable source identities."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path, *, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def default_doc_id(source_sha256: str, *, length: int = 24) -> str:
    if len(source_sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in source_sha256):
        raise ValueError("source_sha256 must be a 64-character hexadecimal digest")
    if length not in {16, 24}:
        raise ValueError("doc_id digest length must be 16 or 24")
    return f"doc_{source_sha256[:length].lower()}"
