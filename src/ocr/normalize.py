"""Normalizers for values read off the invoice image: money, quantities,
dates, NIT, and the DIAN line-wrap join rule for Descripcion. Pure
functions, no OCR/IO here.
"""
from __future__ import annotations

import re
from typing import Optional

from wordfreq import zipf_frequency

_LETTERS = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
_TAIL = re.compile(rf"[{_LETTERS}]+$")
_HEAD = re.compile(rf"^[{_LETTERS}]+")
MIN_ZIPF = 1.0  # frecuencia minima (escala Zipf) para considerar que algo es palabra en espanol


def _is_word(token: str) -> bool:
    return zipf_frequency(token.lower(), "es") >= MIN_ZIPF


def join_wrapped_lines(lines: list[str]) -> str:
    """Joins the lines of a Descripcion cell that the DIAN generator wrapped
    by raw pixel width.

    The cut can land mid-word ("UNILATERA"|"L") or on a space the PDF then
    drops ("OBSTETRICA"|"CON") -- even the PDF's own text layer never
    recovers that dropped space. Decided with a Spanish word-frequency
    dictionary (verified against all 21 real wrapped descriptions in the
    10-invoice sample):
      1. Non-alphabetic edge (digits, parens, dashes): no space.
      2. The fragments joined together form a word ("UNILATERA"+"L"): no space.
      3. Both fragments are words on their own but the join isn't
         ("OBSTETRICA"+"CON"): space.
      4. Otherwise (fragments that aren't words, e.g. "PE"+"RINATOLOGIA"):
         no space.
    """
    lines = [l.strip() for l in lines if l and l.strip()]
    if not lines:
        return ""
    out = lines[0]
    for nxt in lines[1:]:
        left, right = _TAIL.search(out), _HEAD.match(nxt)
        if not left or not right:
            sep = ""
        elif _is_word(left.group() + right.group()):
            sep = ""
        elif _is_word(left.group()) and _is_word(right.group()):
            sep = " "
        else:
            sep = ""
        out = f"{out}{sep}{nxt}"
    return out


def normalize_money(raw: Optional[str]) -> Optional[float]:
    """'$ 75.000,00' -> 75000.0. Colombian format: '.' thousands, ',' decimal."""
    if not raw:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", raw)
    if not cleaned:
        return None
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def normalize_quantity(raw: Optional[str]) -> Optional[float]:
    """Same decimal convention as money: '1,00' -> 1.0."""
    return normalize_money(raw)


def normalize_date(raw: Optional[str]) -> Optional[str]:
    """'05/11/2024' -> '2024-11-05'."""
    if not raw:
        return None
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", raw.strip())
    if not m:
        return None
    day, month, year = m.groups()
    return f"{year}-{month}-{day}"


def normalize_nit(raw: Optional[str]) -> Optional[str]:
    """Digits only, dropping the check digit (DV) after a '-' if present."""
    if not raw:
        return None
    main = raw.split("-")[0]
    digits = re.sub(r"\D", "", main)
    return digits or None
