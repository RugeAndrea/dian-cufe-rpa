"""Compares OCR output against reference sources -- NEVER used for
extraction, only to measure accuracy:
  - header fields  -> output/metadata/dian_detalle.json (read off the live
    DIAN page in numeral 1, independent of the PDF/OCR)
  - product table  -> the PDF's own text layer, via PyMuPDF's
    page.find_tables() (best-effort; it's a cross-check, not ground truth)
  - CUFE           -> the input CUFE string itself (CER)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from .normalize import normalize_date, normalize_money, normalize_nit, normalize_quantity


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def cer(hypothesis: Optional[str], reference: Optional[str]) -> Optional[float]:
    if not reference:
        return None
    return levenshtein(hypothesis or "", reference) / len(reference)


def _ref_fecha_to_iso(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", raw.strip())
    if not m:
        return None
    day, month, year = m.groups()
    return f"{year}-{month}-{day}"


def validate_header(
    numero_factura: Optional[str],
    fecha_emision: Optional[str],
    nit_emisor: Optional[str],
    cufe_ocr: Optional[str],
    cufe_input: str,
    metadata_entry: Optional[dict],
) -> dict:
    result = {
        "numero_factura_ok": None,
        "fecha_emision_ok": None,
        "nit_emisor_ok": None,
        "cufe_cer": cer(cufe_ocr, cufe_input),
    }
    if not metadata_entry:
        return result

    ref_numero = f"{metadata_entry.get('serie') or ''}{metadata_entry.get('folio') or ''}"
    ocr_numero = (numero_factura or "").replace("-", "")
    if ref_numero:
        result["numero_factura_ok"] = ocr_numero == ref_numero

    ref_fecha_iso = _ref_fecha_to_iso(metadata_entry.get("fecha_emision"))
    if ref_fecha_iso:
        result["fecha_emision_ok"] = fecha_emision == ref_fecha_iso

    ref_nit = metadata_entry.get("emisor_nit")
    if ref_nit:
        result["nit_emisor_ok"] = normalize_nit(nit_emisor) == ref_nit

    return result


def extract_pdf_reference_products(pdf_path: Path, nit: str) -> list[dict]:
    """Best-effort reference extraction straight from the PDF's text layer,
    used ONLY to validate the OCR'd table -- never as the extraction path
    itself."""
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return []

    try:
        if doc.is_encrypted and not doc.authenticate(nit):
            return []

        page = doc.load_page(0)
        tables = page.find_tables()
        for table in tables.tables:
            rows = table.extract()
            header_idx = next(
                (i for i, row in enumerate(rows) if row and str(row[0] or "").strip() == "Nro."),
                None,
            )
            if header_idx is None:
                continue

            header = rows[header_idx]
            col_map: dict[str, int] = {}
            for i, cell in enumerate(header):
                norm = (cell or "").strip().lower()
                if norm == "código" or norm == "codigo":
                    col_map["codigo"] = i
                elif norm.startswith("descripci"):
                    col_map["descripcion"] = i
                elif norm == "cantidad":
                    col_map["cantidad"] = i
                elif norm == "precio unitario":
                    col_map["precio_unitario"] = i

            products = []
            for row in rows[header_idx + 1:]:
                if not row or not str(row[0] or "").strip().isdigit():
                    continue
                products.append(
                    {
                        "codigo": str(row[col_map["codigo"]] or "").strip() if "codigo" in col_map else None,
                        "descripcion": (
                            str(row[col_map["descripcion"]] or "").replace("\n", "").strip()
                            if "descripcion" in col_map
                            else None
                        ),
                        "cantidad": normalize_quantity(row[col_map["cantidad"]]) if "cantidad" in col_map else None,
                        "precio_unitario": (
                            normalize_money(row[col_map["precio_unitario"]])
                            if "precio_unitario" in col_map
                            else None
                        ),
                    }
                )
            if products:
                return products
        return []
    except Exception:
        return []
    finally:
        doc.close()


def _norm_text(value) -> str:
    """None and '' both mean "no value" -- they must compare equal, not as a
    mismatch (verified on invoice 7: codigo is genuinely blank in both the
    OCR output and the PDF reference, but the old comparison reported 0%
    because the OCR side normalizes a blank cell to None while the
    reference extractor keeps it as '', and None != '')."""
    return "" if value is None else str(value)


def item_has_error(item: dict, ref: dict) -> bool:
    """True if ANY field differs from the reference (empty-vs-empty is not
    an error). Used both to calibrate the confianza_min threshold and to
    report requiere_revision's precision/recall against real errors."""
    if ref.get("codigo") is not None and _norm_text(item.get("codigo")) != _norm_text(ref.get("codigo")):
        return True
    if ref.get("descripcion") is not None and _norm_text(item.get("descripcion")) != _norm_text(ref.get("descripcion")):
        return True
    if ref.get("cantidad") is not None and item.get("cantidad") != ref.get("cantidad"):
        return True
    if ref.get("precio_unitario") is not None and item.get("precio_unitario") != ref.get("precio_unitario"):
        return True
    return False


def validate_products(items: list[dict], reference: list[dict]) -> dict:
    n = max(len(items), len(reference))
    if n == 0:
        return {"codigo_pct": None, "descripcion_pct": None, "descripcion_cer_avg": None,
                "cantidad_pct": None, "precio_unitario_pct": None, "n_items_ocr": 0, "n_items_referencia": 0}

    codigo_ok = desc_ok = cant_ok = precio_ok = 0
    cers = []
    for i in range(n):
        item = items[i] if i < len(items) else {}
        ref = reference[i] if i < len(reference) else {}

        if ref.get("codigo") is not None:
            codigo_ok += int(_norm_text(item.get("codigo")) == _norm_text(ref.get("codigo")))
        if ref.get("descripcion") is not None:
            desc_ok += int(_norm_text(item.get("descripcion")) == _norm_text(ref.get("descripcion")))
            cers.append(cer(item.get("descripcion"), ref.get("descripcion")))
        if ref.get("cantidad") is not None:
            cant_ok += int(item.get("cantidad") == ref.get("cantidad"))
        if ref.get("precio_unitario") is not None:
            precio_ok += int(item.get("precio_unitario") == ref.get("precio_unitario"))

    cers = [c for c in cers if c is not None]
    return {
        "codigo_pct": round(100 * codigo_ok / n, 1),
        "descripcion_pct": round(100 * desc_ok / n, 1),
        "descripcion_cer_avg": round(sum(cers) / len(cers), 4) if cers else None,
        "cantidad_pct": round(100 * cant_ok / n, 1),
        "precio_unitario_pct": round(100 * precio_ok / n, 1),
        "n_items_ocr": len(items),
        "n_items_referencia": len(reference),
    }
