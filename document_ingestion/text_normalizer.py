"""Deterministic cleanup for Word text before raw-schema serialization."""

from __future__ import annotations

import re

_ALLOWED_CONTROL_CHARACTERS = {"\n", "\t"}


def normalize_word_text(text: str) -> tuple[str, list[str]]:
    """Normalize Word-specific separators while recording applied operations.

    Word uses ASCII 30 inside generated captions such as ``图5\x1e3``.  The
    visible semantic form is ``图5-3``.  Remaining C0 controls are removed so
    parser output never carries hidden control bytes into Spark or embeddings.
    """

    operations: list[str] = []

    if "\u200b" in text or "\ufeff" in text:
        text = text.replace("\u200b", "").replace("\ufeff", "")
        operations.append("removed_zero_width_characters")

    if "\r\x07" in text or "\x07" in text:
        text = text.replace("\r\x07", "\n").replace("\x07", "")
        operations.append("removed_word_cell_markers")

    # A paragraph/story Range normally ends with one structural carriage
    # return.  Removing that terminator is expected parsing behavior rather
    # than a meaningful text transformation, so it is intentionally not
    # recorded in extra.text_normalizations.
    if text.endswith("\r"):
        text = text[:-1]

    if "\x1e" in text:
        text = re.sub(r"(?<=\S)\x1e(?=\S)", "-", text)
        text = text.replace("\x1e", " ")
        operations.append("normalized_word_caption_separator")

    if "\x0b" in text or "\x0c" in text or "\r" in text:
        text = text.replace("\x0b", "\n").replace("\x0c", "\n").replace("\r", "\n")
        operations.append("normalized_word_line_breaks")

    cleaned_characters: list[str] = []
    removed_other_control = False
    for character in text:
        if ord(character) < 32 and character not in _ALLOWED_CONTROL_CHARACTERS:
            removed_other_control = True
            continue
        cleaned_characters.append(character)
    if removed_other_control:
        operations.append("removed_other_control_characters")

    normalized = "".join(cleaned_characters).strip("\r\n\t ")
    return normalized, list(dict.fromkeys(operations))
