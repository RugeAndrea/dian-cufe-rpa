"""Extracts 'Numero de Factura', 'Fecha de Emision' and 'Nit del Emisor' from
page 1 via OCR (lang=spa, --psm 4), plus the CUFE (for validation only).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .config import OCR_CONFIG
from .engine import Word, image_to_text, image_to_words


def _match(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


def _section(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start == -1:
        return text
    end = text.find(end_marker, start + len(start_marker))
    return text[start:] if end == -1 else text[start:end]


def _confidence_for_value(words: list[Word], value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    target = re.sub(r"\s+", "", value)
    matches = [
        w.conf
        for w in words
        if target and (target in w.text.replace(" ", "") or w.text.replace(" ", "") in target)
    ]
    return min(matches) if matches else None


def extract_header(image_path: Path) -> dict:
    text = image_to_text(image_path, psm=OCR_CONFIG.psm_header)
    words = image_to_words(image_path, psm=OCR_CONFIG.psm_header)

    numero_factura = _match(OCR_CONFIG.numero_factura_regex, text)
    fecha_emision = _match(OCR_CONFIG.fecha_emision_regex, text)

    emisor_text = _section(
        text, OCR_CONFIG.emisor_section_marker, OCR_CONFIG.adquiriente_section_marker
    )
    nit_emisor = _match(OCR_CONFIG.nit_emisor_regex, emisor_text)

    cufe_match = re.search(OCR_CONFIG.cufe_regex, re.sub(r"\s", "", text))
    cufe_ocr = cufe_match.group(0) if cufe_match else None

    confidences = [
        c
        for c in (
            _confidence_for_value(words, numero_factura),
            _confidence_for_value(words, fecha_emision),
            _confidence_for_value(words, nit_emisor),
        )
        if c is not None
    ]

    return {
        "numero_factura_raw": numero_factura,
        "fecha_emision_raw": fecha_emision,
        "nit_emisor_raw": nit_emisor,
        "cufe_ocr": cufe_ocr,
        "confianza_min_encabezado": min(confidences) if confidences else None,
    }
