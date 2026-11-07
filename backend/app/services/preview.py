"""Sidebar previews: one line of readable text per tree node.

Assistant replies are Markdown (rendered by the frontend, U1), but the tree
only shows a short plain-text label, so `**bold**`, backticks, `#` headings and
list markers would show up as literal syntax. `markdown_to_text` removes that
syntax with a few line-level and inline regexes. It is deliberately not a full
Markdown parser: a preview only needs to be readable, never exact, and this
keeps the backend dependency-free. User turns are plain text (the UI shows them
verbatim), so their previews are never stripped.
"""

import re

from app.db.models import Role

# Line-level syntax (applied with re.MULTILINE). `[ \t]` rather than `\s` so a
# pattern never reaches across line breaks.
_FENCE = re.compile(r"^[ \t]*(```|~~~).*$", re.MULTILINE)  # ```python → dropped, code kept
_RULE = re.compile(r"^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$", re.MULTILINE)  # --- / *** / ___
_TABLE_SEP = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$", re.MULTILINE
)
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_QUOTE = re.compile(r"^[ \t]{0,3}(>[ \t]?)+", re.MULTILINE)
_LIST = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?", re.MULTILINE)

# Inline syntax. Order matters: images before links, code before emphasis
# (so `a*b*c` inside backticks keeps its asterisks).
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_CODE = re.compile(r"(`+)(.+?)\1")
_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_EM_STAR = re.compile(r"\*(?=\S)(.+?)(?<=\S)\*")
# `_x_` only at word boundaries, so snake_case_names survive.
_EM_UNDER = re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)")


def markdown_to_text(content: str) -> str:
    """Strip common Markdown syntax, keeping the visible text."""
    # NUL marks the inline-code placeholders below, so it must not occur in the
    # input. New rows never contain it (`clean_text`), but SQLite databases from
    # before Q26 may, and a stray "\x000\x00" used to crash /tree.
    text = _FENCE.sub("", content.replace("\x00", ""))
    text = _RULE.sub("", text)
    text = _TABLE_SEP.sub("", text)
    text = _HEADING.sub("", text)
    text = _QUOTE.sub("", text)
    text = _LIST.sub("", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)

    # Pull inline code out first so emphasis rules never touch its contents.
    codes: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        codes.append(m.group(2).strip())
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE.sub(_stash, text)
    text = _STRONG.sub(r"\2", text)
    text = _STRIKE.sub(r"\1", text)
    text = _EM_STAR.sub(r"\1", text)
    text = _EM_UNDER.sub(r"\1", text)
    return re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], text)


def make_preview(content: str, role: str, limit: int) -> str:
    """Single-line, length-capped preview for a sidebar node.

    Markdown is stripped *before* truncating, so the cap counts visible
    characters and never cuts a `**` pair in half.
    """
    text = markdown_to_text(content) if role == Role.assistant else content.replace("\x00", "")
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
