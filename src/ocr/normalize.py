"""Normalizers for values read off the invoice image: money, quantities,
dates and NIT. Pure functions, no OCR/IO here.
"""
from __future__ import annotations

import re
from typing import Optional


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
