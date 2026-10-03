"""Integration test over the real invoice 1 image. Skips if numeral 1 (the
RPA) hasn't produced output/pdfs/01_*.pdf yet, since output/ is gitignored
and not part of a fresh checkout.
"""
from pathlib import Path

import pytest

from src.ocr.exporter import render_pdf_to_png
from src.ocr.header import extract_header
from src.ocr.table import detect_table_structure, ocr_table_items
from src.rpa.config import CONFIG as RPA_CONFIG

CUFE_1 = "eccc372e4bdb8125cf2e896b405ddb91918c7c6d92238892e3b36bd3b6a86929a3c47ed18ae8f559a1bf51b89ab560f1"
NIT_1 = "900156264"


def _invoice1_pdf() -> Path:
    return RPA_CONFIG.pdf_dir / f"01_{CUFE_1[:12]}.pdf"


@pytest.mark.skipif(not _invoice1_pdf().exists(), reason="output/pdfs/01_*.pdf no existe (correr la RPA primero)")
def test_invoice1_end_to_end_header_and_table():
    pdf_path = _invoice1_pdf()
    image_paths = render_pdf_to_png(pdf_path, NIT_1, 1, CUFE_1)
    assert len(image_paths) >= 1

    header = extract_header(image_paths[0])
    assert header["numero_factura_raw"] == "FEBQ-243564"
    assert header["fecha_emision_raw"] == "05/11/2024"
    assert header["nit_emisor_raw"] == "800033723"
    assert header["cufe_ocr"] == CUFE_1

    structure = detect_table_structure(image_paths, pdf_path, NIT_1, 1, CUFE_1)
    assert structure["found"] is True

    items = ocr_table_items(structure)
    assert len(items) == 1
    assert items[0]["codigo_raw"] == "0"
    # Tesseract merges the lone "L" and "O" tokens into "LO" regardless of
    # psm/oem/preserve_interword_spaces (tried all combinations, see
    # docs/ocr_tabla.md) -- a documented limitation, not a regression.
    assert items[0]["descripcion_raw"] == "MAMOGRAFIA UNILATERALO PIEZA QUIRURGICA"
    assert items[0]["cantidad_raw"].replace(".", ",") in ("1,00",)
    assert "75.000" in items[0]["precio_unitario_raw"]
