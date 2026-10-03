"""CLI entrypoint.

Usage:
    python -m src.main rpa [--limit N] [--only N] [--force]
"""
from __future__ import annotations

import argparse
import csv
import random
import time
from pathlib import Path
from typing import Optional

from patchright.sync_api import sync_playwright

from src.common.logging_setup import setup_logging
from src.common.metrics import write_metrics
from src.ocr.config import OCR_CONFIG
from src.ocr.pipeline import run_ocr_batch
from src.rpa.config import CONFIG
from src.rpa.dian_client import CufeResult, SearchError, process_cufe
from src.rpa.downloader import DownloadError
from src.rpa.metadata import load_metadata_store, save_metadata_entry


def load_rows(limit: Optional[int], only: Optional[int]) -> list[dict]:
    with open(CONFIG.input_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if only is not None:
        rows = [r for r in rows if int(r["n"]) == only]
    elif limit is not None:
        rows = rows[:limit]
    return rows


def pdf_path_for(n: int, cufe: str) -> Path:
    return CONFIG.pdf_dir / f"{n:02d}_{cufe[:12]}.pdf"


def is_valid_existing_pdf(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with path.open("rb") as f:
            return f.read(4) == b"%PDF"
    except OSError:
        return False


def analyze_pdf(
    path: Path, nit_input: str, emisor_nit: Optional[str], logger
) -> tuple[Optional[int], Optional[bool], Optional[bool], Optional[str]]:
    """Opens the PDF exactly as DIAN delivered it (never rewritten/decrypted
    to disk) and reports (paginas, cifrado, tiene_capa_texto, password_ok_con).

    Uses PyMuPDF because it supports AES-encrypted PDFs natively, unlike
    pypdf in this environment. Tries the search NIT first (confirmed correct
    by the user), then falls back to the issuer's NIT read from the detail
    page, since the DIAN bootbox says the password can be either party's NIT.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("pymupdf no esta instalado, no se puede analizar %s", path)
        return None, None, None, None

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        logger.warning("no se pudo abrir el PDF %s: %r", path, exc)
        return None, None, None, None

    cifrado = bool(doc.is_encrypted)
    password_ok_con = None

    if cifrado:
        candidates = [("input", nit_input)]
        if emisor_nit and emisor_nit != nit_input:
            candidates.append(("emisor", emisor_nit))

        opened = False
        for label, password in candidates:
            if not password:
                continue
            try:
                if doc.authenticate(password):
                    password_ok_con = label
                    opened = True
                    break
            except Exception:
                pass

        if not opened:
            logger.warning(
                "PDF %s esta cifrado y no abrio con ninguna contrasena probada (%s)",
                path.name, [c for c, _ in candidates],
            )
            doc.close()
            return None, True, None, None

    paginas = doc.page_count
    texto = doc.load_page(0).get_text() if paginas else ""
    tiene_texto = bool(texto.strip())
    doc.close()
    return paginas, cifrado, tiene_texto, password_ok_con


def run_cufe(playwright, row: dict, logger, metadata_store: dict, force: bool) -> CufeResult:
    n = int(row["n"])
    cufe = row["cufe"]
    nit = row["nit"]
    result = CufeResult(n=n, cufe=cufe, nit=nit)

    out_path = pdf_path_for(n, cufe)
    if not force and is_valid_existing_pdf(out_path):
        logger.info("[n=%s] CUFE %s... ya tiene un PDF valido en %s, se omite", n, cufe[:12], out_path)
        result.estado = "omitido"
        result.bytes = out_path.stat().st_size
        known_emisor_nit = (metadata_store.get(cufe) or {}).get("emisor_nit")
        paginas, cifrado, tiene_texto, password_ok_con = analyze_pdf(out_path, nit, known_emisor_nit, logger)
        result.paginas, result.cifrado, result.tiene_capa_texto, result.password_ok_con = (
            paginas, cifrado, tiene_texto, password_ok_con,
        )
        return result

    for attempt in range(1, CONFIG.max_attempts + 1):
        result.intentos = attempt
        logger.info("[n=%s] intento %s/%s para CUFE %s...", n, attempt, CONFIG.max_attempts, cufe[:12])

        browser = playwright.chromium.launch(
            channel=CONFIG.chrome_channel,
            headless=CONFIG.headless,
            args=list(CONFIG.browser_args),
        )
        context = browser.new_context(accept_downloads=True)
        total_start = time.perf_counter()
        try:
            process_result = process_cufe(context, cufe, nit, result)

            CONFIG.pdf_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(process_result.pdf_bytes)

            save_metadata_entry(CONFIG.metadata_file, cufe, process_result.metadata)
            metadata_store[cufe] = process_result.metadata

            emisor_nit = process_result.metadata.get("emisor_nit")
            paginas, cifrado, tiene_texto, password_ok_con = analyze_pdf(out_path, nit, emisor_nit, logger)
            result.paginas, result.cifrado, result.tiene_capa_texto, result.password_ok_con = (
                paginas, cifrado, tiene_texto, password_ok_con,
            )
            result.estado = "ok"
            result.error = None

            logger.info(
                "[n=%s] PDF descargado via %s desde %s (%s bytes) -> %s",
                n, result.metodo_descarga, result.endpoint_descarga, result.bytes, out_path,
            )
            logger.info(
                "[n=%s] password_ok_con=%s paginas=%s tiene_capa_texto=%s",
                n, result.password_ok_con, result.paginas, result.tiene_capa_texto,
            )
            break
        except (SearchError, DownloadError) as exc:
            result.estado = "error"
            result.error = str(exc)
            logger.warning("[n=%s] intento %s/%s fallo: %s", n, attempt, CONFIG.max_attempts, result.error)
        except Exception as exc:
            result.estado = "error"
            result.error = repr(exc)
            logger.exception("[n=%s] error inesperado en intento %s", n, attempt)
        finally:
            context.close()
            browser.close()
            result.t_total = round(time.perf_counter() - total_start, 3)

        if result.estado != "ok" and attempt < CONFIG.max_attempts:
            backoff = CONFIG.backoff_base_s * (2 ** (attempt - 1))
            logger.info("[n=%s] reintentando en %.1fs", n, backoff)
            time.sleep(backoff)

    return result


def cmd_rpa(args: argparse.Namespace) -> None:
    logger = setup_logging(CONFIG.log_dir)
    rows = load_rows(limit=args.limit, only=args.only)
    logger.info("procesando %s CUFE(s) (limit=%s, only=%s, force=%s)", len(rows), args.limit, args.only, args.force)

    metadata_store = load_metadata_store(CONFIG.metadata_file)

    results: list[dict] = []
    with sync_playwright() as playwright:
        for i, row in enumerate(rows):
            result = run_cufe(playwright, row, logger, metadata_store, args.force)
            results.append(result.__dict__)
            logger.info(
                "[n=%s] estado=%s t_total=%ss intentos=%s error=%s",
                result.n, result.estado, result.t_total, result.intentos, result.error,
            )
            if i < len(rows) - 1:
                pause = random.uniform(CONFIG.pause_min_s, CONFIG.pause_max_s)
                logger.info("pausa de %.1fs antes del siguiente CUFE", pause)
                time.sleep(pause)

    summary = write_metrics(results, CONFIG.metrics_file)["resumen"]
    logger.info(
        "resumen: %s/%s exitos, promedio=%ss, mediana=%ss, min=%ss, max=%ss",
        summary["exitos"], summary["total"], summary["promedio_t_total"],
        summary["mediana_t_total"], summary["minimo_t_total"], summary["maximo_t_total"],
    )
    logger.info("metricas escritas en %s", CONFIG.metrics_file)


def cmd_ocr(args: argparse.Namespace) -> None:
    logger = setup_logging(CONFIG.log_dir)
    rows = load_rows(limit=args.limit, only=args.only)
    logger.info("OCR: procesando %s factura(s) (limit=%s, only=%s, debug=%s)", len(rows), args.limit, args.only, args.debug)

    metadata_store = load_metadata_store(CONFIG.metadata_file)

    batch = run_ocr_batch(rows, metadata_store, CONFIG.pdf_dir, debug=args.debug)

    for r in batch["results"]:
        logger.info(
            "[n=%s] estado=%s items=%s t_total=%ss ram_pico=%sMB error=%s",
            r["n"], r["estado"], r["n_items"], r["tiempos"].get("total"), r["ram_pico_mb"], r["error"],
        )

    summary = batch["metrics"]["resumen"]
    logger.info(
        "resumen OCR: %s/%s exitos, promedio=%ss, mediana=%ss, min=%ss, max=%ss, facturas/min=%s",
        summary["exitos"], summary["total_facturas"], summary["promedio_t_total"],
        summary["mediana_t_total"], summary["minimo_t_total"], summary["maximo_t_total"],
        summary["facturas_por_minuto"],
    )
    logger.info("CSV consolidado en %s", OCR_CONFIG.consolidado_csv)
    logger.info("metricas OCR en %s, validacion en %s", OCR_CONFIG.metrics_file, OCR_CONFIG.validation_file)


def cmd_all(args: argparse.Namespace) -> None:
    cmd_rpa(args)
    cmd_ocr(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.main")
    subparsers = parser.add_subparsers(dest="command", required=True)

    rpa_parser = subparsers.add_parser("rpa", help="Descarga los PDF de la DIAN para los CUFE en data/input.csv")
    rpa_parser.add_argument("--limit", type=int, default=None, help="Procesar solo los primeros N CUFE")
    rpa_parser.add_argument("--only", type=int, default=None, help="Procesar solo el CUFE con este numero (columna n)")
    rpa_parser.add_argument("--force", action="store_true", help="Volver a descargar aunque ya exista un PDF valido")
    rpa_parser.set_defaults(func=cmd_rpa)

    ocr_parser = subparsers.add_parser("ocr", help="Extrae encabezado y productos por OCR de los PDF ya descargados")
    ocr_parser.add_argument("--only", type=int, default=None, help="Procesar solo la factura con este numero (columna n)")
    ocr_parser.add_argument("--limit", type=int, default=None, help="Procesar solo las primeras N facturas")
    ocr_parser.add_argument("--debug", action="store_true", help="Guardar recortes/mascaras de depuracion en output/debug")
    ocr_parser.set_defaults(func=cmd_ocr)

    all_parser = subparsers.add_parser("all", help="Corre rpa y luego ocr")
    all_parser.add_argument("--limit", type=int, default=None)
    all_parser.add_argument("--only", type=int, default=None)
    all_parser.add_argument("--force", action="store_true")
    all_parser.add_argument("--debug", action="store_true")
    all_parser.set_defaults(func=cmd_all)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
