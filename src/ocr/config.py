"""Centralized configuration for the OCR pipeline: DPI, language, thresholds
and regexes. Values can be overridden via environment variables, reusing the
same helpers as src/rpa/config.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..rpa.config import BASE_DIR, _env, _env_float, _env_int, _env_path  # noqa: F401


@dataclass(frozen=True)
class OcrConfig:
    # --- Rendering (PDF -> PNG) ---
    dpi: int = _env_int("OCR_DPI", 300)

    # --- Tesseract ---
    lang: str = _env("OCR_LANG", "spa")
    psm_header: int = _env_int("OCR_PSM_HEADER", 4)
    psm_table_body: int = _env_int("OCR_PSM_TABLE_BODY", 6)
    numeric_whitelist: str = "0123456789.,$"
    codigo_whitelist: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-./"
    # Measured both on the 10-invoice batch (see docs/ocr_tabla.md):
    # tessdata_best raises codigo (73%->86%) and descripcion (81.7%->85%,
    # CER 0.79%->0.57%), but it also pushes per-word confidence so far down
    # that confianza_min falls under the fixed 80 threshold on EVERY row,
    # including invoice 1's otherwise-perfect extraction (80.7 -> 35.3) --
    # with that threshold required to stay at 80, requiere_revision would
    # flag every single row regardless of actual correctness, which defeats
    # its purpose. It also regresses CUFE recognition (CER 1.15%->60%) and
    # runs ~40% slower. Net: the default ("fast") model is the one whose
    # confidence scores are actually usable against a fixed threshold, so
    # it stays the default. Set OCR_TESSDATA_DIR=/usr/share/tessdata-best to
    # use the best model instead.
    tessdata_dir: str = _env("OCR_TESSDATA_DIR", "")

    # --- Quality thresholds ---
    confidence_threshold: float = _env_float("OCR_CONFIDENCE_THRESHOLD", 80.0)

    # --- Header regexes (tolerant to accents/whitespace) ---
    cufe_regex: str = r"[0-9a-fA-F]{96}"
    numero_factura_regex: str = r"N[uú]mero de Factura:\s*([A-Z0-9\-]+)"
    fecha_emision_regex: str = r"Fecha de Emisi[oó]n:\s*(\d{2}/\d{2}/\d{4})"
    nit_emisor_regex: str = r"Nit del Emisor:\s*([\d.\-]+)"

    emisor_section_marker: str = "Datos del Emisor"
    adquiriente_section_marker: str = "Datos del Adquiriente"

    # --- Products table markers / column headers ---
    table_start_marker: str = "Detalles"
    table_end_markers: tuple = ("Notas", "Datos Totales", "Descuentos", "Referencias")
    column_headers: tuple = ("Codigo", "Descripcion", "Cantidad", "Precio unitario")
    # "Precio unitario de venta" must NOT match "Precio unitario"
    column_header_exact: dict = field(
        default_factory=lambda: {
            "codigo": "Código",
            "descripcion": "Descripción",
            "cantidad": "Cantidad",
            "precio_unitario": "Precio unitario",
        }
    )

    # --- Paths ---
    images_dir: Path = _env_path("OCR_IMAGES_DIR", "output/images")
    csv_dir: Path = _env_path("OCR_CSV_DIR", "output/csv")
    debug_dir: Path = _env_path("OCR_DEBUG_DIR", "output/debug")

    @property
    def metrics_file(self):
        return BASE_DIR / "output" / "metrics" / "ocr_metrics.json"

    @property
    def validation_file(self):
        return BASE_DIR / "output" / "metrics" / "ocr_validation.json"

    @property
    def consolidado_csv(self):
        return self.csv_dir / "consolidado.csv"


OCR_CONFIG = OcrConfig()
