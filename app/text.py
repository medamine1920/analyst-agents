"""Text normalization shared by the agents and the eval scorer."""

from __future__ import annotations

import unicodedata


def normalize(text: str) -> str:
    """Map every Unicode space to " " and every dash or minus sign to "-"."""
    text = unicodedata.normalize("NFKC", text)
    out = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Zs":
            out.append(" ")
        elif category == "Pd" or char == "\u2212":
            out.append("-")
        else:
            out.append(char)
    return "".join(out)
