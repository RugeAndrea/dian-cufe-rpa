"""Orchestrates the OCR pipeline for one invoice (render -> header -> table
-> normalize -> CSV) and for a full batch (+ consolidated CSV, metrics,
validation).
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import psutil

from ..common.metrics import stage_timer
from ..rpa.config import BASE_DIR
from .config import OCR_CONFIG
from .exporter import PdfOpenError, render_pdf_to_png
from .header import extract_header
from .normalize import normalize_date, normalize_money, normalize_nit, normalize_quantity
from .table import detect_table_structure, ocr_table_items
from .validate import extract_pdf_reference_products, validate_header, validate_products


def _rss_mb() -> float:
    return psutil.Process().memory_info().rss / (1024 * 1024)


def process_invoice(
    n: int,
    cufe: str,
    nit: str,
    pdf_path: Path,
    metadata_entry: Optional[dict],
    debug: bool = False,
) -> dict:
    tiempos: dict[str, float] = {}
    peak_rss = _rss_mb()

    def track() -> None:
        nonlocal peak_rss
        peak_rss = max(peak_rss, _rss_mb())

    debug_dir = OCR_CONFIG.debug_dir if debug else None
    debug_prefix = f"{n:02d}_"

    estado = "ok"
    error: Optional[str] = None
    numero_factura = fecha_emision = nit_emisor = None
    header_data: dict = {}
    items: list[dict] = []
    df = pd.DataFrame()

    t_total_start = time.perf_counter()
    try:
        with stage_timer() as t:
            image_paths = render_pdf_to_png(pdf_path, nit, n, cufe)
        tiempos["render"] = t["seconds"]
        track()

        with stage_timer() as t:
            header_data = extract_header(image_paths[0])
        tiempos["ocr_encabezado"] = t["seconds"]
        track()

        with stage_timer() as t:
            structure = detect_table_structure(
                image_paths, pdf_path, nit, n, cufe, debug_dir=debug_dir, debug_prefix=debug_prefix
            )
        tiempos["deteccion_tabla"] = t["seconds"]
        track()

        with stage_timer() as t:
            items_raw = ocr_table_items(structure) if structure.get("found") else []
        tiempos["ocr_tabla"] = t["seconds"]
        track()

        with stage_timer() as t:
            numero_factura = header_data.get("numero_factura_raw")
            fecha_emision = normalize_date(header_data.get("fecha_emision_raw"))
            nit_emisor = normalize_nit(header_data.get("nit_emisor_raw"))
            confianza_encabezado = header_data.get("confianza_min_encabezado")

            for raw in items_raw:
                cantidad = normalize_quantity(raw.get("cantidad_raw"))
                precio = normalize_money(raw.get("precio_unitario_raw"))
                item_confs = [c for c in (confianza_encabezado, raw.get("confianza_min")) if c is not None]
                confianza_min_item = min(item_confs) if item_confs else None
                requiere_revision = bool(
                    (confianza_min_item is not None and confianza_min_item < OCR_CONFIG.confidence_threshold)
                    or confianza_min_item is None
                    or cantidad is None
                    or precio is None
                    or not raw.get("codigo_raw")
                    or not raw.get("descripcion_raw")
                    or not numero_factura
                    or not fecha_emision
                    or not nit_emisor
                )
                items.append(
                    {
                        "codigo": raw.get("codigo_raw") or None,
                        "descripcion": raw.get("descripcion_raw") or None,
                        "cantidad": cantidad,
                        "precio_unitario": precio,
                        "confianza_min": confianza_min_item,
                        "requiere_revision": requiere_revision,
                    }
                )

            if not items:
                items.append(
                    {
                        "codigo": None,
                        "descripcion": None,
                        "cantidad": None,
                        "precio_unitario": None,
                        "confianza_min": confianza_encabezado,
                        "requiere_revision": True,
                    }
                )
        tiempos["parseo"] = t["seconds"]
        track()

        with stage_timer() as t:
            df = pd.DataFrame(
                [
                    {
                        "archivo": pdf_path.name,
                        "cufe": cufe,
                        "numero_factura": numero_factura,
                        "fecha_emision": fecha_emision,
                        "nit_emisor": nit_emisor,
                        **item,
                    }
                    for item in items
                ]
            )
            OCR_CONFIG.csv_dir.mkdir(parents=True, exist_ok=True)
            safe_numero = (numero_factura or cufe[:12]).replace("/", "-")
            csv_path = OCR_CONFIG.csv_dir / f"{n:02d}_{safe_numero}.csv"
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        tiempos["exportacion"] = t["seconds"]
        track()

    except Exception as exc:
        estado = "error"
        error = repr(exc)

    tiempos["total"] = round(time.perf_counter() - t_total_start, 3)

    validacion: dict = {}
    try:
        header_validation = validate_header(
            numero_factura, fecha_emision, nit_emisor,
            header_data.get("cufe_ocr"), cufe, metadata_entry,
        )
        reference_products = extract_pdf_reference_products(pdf_path, nit)
        products_validation = validate_products(items, reference_products)
        validacion = {**header_validation, **products_validation}
    except Exception as exc:
        validacion = {"error": repr(exc)}

    return {
        "n": n,
        "cufe": cufe,
        "archivo": pdf_path.name,
        "numero_factura": numero_factura,
        "fecha_emision": fecha_emision,
        "nit_emisor": nit_emisor,
        "n_items": len(items),
        "estado": estado,
        "error": error,
        "tiempos": tiempos,
        "ram_pico_mb": round(peak_rss, 1),
        "df": df,
        "validacion": validacion,
    }


def _rpa_projection(ocr_avg: Optional[float]) -> Optional[dict]:
    rpa_metrics_path = BASE_DIR / "output" / "metrics" / "rpa_metrics.json"
    if not rpa_metrics_path.exists() or ocr_avg is None:
        return None
    try:
        rpa_data = json.loads(rpa_metrics_path.read_text(encoding="utf-8"))
        rpa_avg = rpa_data.get("resumen", {}).get("promedio_t_total")
    except (OSError, json.JSONDecodeError):
        return None
    if rpa_avg is None:
        return None
    combinado = rpa_avg + ocr_avg
    return {
        "promedio_rpa_s": rpa_avg,
        "promedio_ocr_s": ocr_avg,
        "promedio_combinado_s": round(combinado, 3),
        "horas_estimadas_1000_facturas": round(combinado * 1000 / 3600, 2),
    }


def _metrics_summary(results: list[dict]) -> dict:
    totals = [r["tiempos"]["total"] for r in results if r.get("estado") == "ok"]
    promedio = round(statistics.mean(totals), 3) if totals else None
    return {
        "exitos": sum(1 for r in results if r.get("estado") == "ok"),
        "total_facturas": len(results),
        "promedio_t_total": promedio,
        "mediana_t_total": round(statistics.median(totals), 3) if totals else None,
        "minimo_t_total": round(min(totals), 3) if totals else None,
        "maximo_t_total": round(max(totals), 3) if totals else None,
        "suma_t_total": round(sum(totals), 3) if totals else None,
        "facturas_por_minuto": round(60 / promedio, 2) if promedio else None,
        "proyeccion_1000_facturas": _rpa_projection(promedio),
    }


def run_ocr_batch(rows: list[dict], metadata_store: dict, pdf_dir: Path, debug: bool = False) -> dict:
    results = []
    dfs = []
    validaciones = {}

    for row in rows:
        n = int(row["n"])
        cufe = row["cufe"]
        nit = row["nit"]
        pdf_path = pdf_dir / f"{n:02d}_{cufe[:12]}.pdf"
        metadata_entry = metadata_store.get(cufe)

        result = process_invoice(n, cufe, nit, pdf_path, metadata_entry, debug=debug)
        dfs.append(result.pop("df"))
        validaciones[cufe] = result.pop("validacion")
        results.append(result)

    consolidated = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    OCR_CONFIG.csv_dir.mkdir(parents=True, exist_ok=True)
    consolidated.to_csv(OCR_CONFIG.consolidado_csv, index=False, encoding="utf-8-sig")

    metrics_payload = {"facturas": results, "resumen": _metrics_summary(results)}
    OCR_CONFIG.metrics_file.parent.mkdir(parents=True, exist_ok=True)
    OCR_CONFIG.metrics_file.write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    OCR_CONFIG.validation_file.parent.mkdir(parents=True, exist_ok=True)
    OCR_CONFIG.validation_file.write_text(
        json.dumps(validaciones, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {"results": results, "metrics": metrics_payload, "validaciones": validaciones}
