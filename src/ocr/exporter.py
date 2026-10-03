"""Renders each page of a (DIAN-encrypted) PDF to a grayscale PNG at a fixed
DPI. This is the ONLY place that touches the PDF's text layer for anything
other than validation -- the render itself is purely visual, OCR is what
reads it afterwards.
"""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from .config import OCR_CONFIG


class PdfOpenError(Exception):
    pass


def render_pdf_to_png(pdf_path: Path, nit: str, n: int, cufe: str) -> list[Path]:
    """Opens pdf_path (authenticating with `nit`) and renders every page to
    output/images/{n:02d}_{cufe[:12]}_p{k}.png at OCR_CONFIG.dpi, grayscale.

    Returns the list of PNG paths, in page order (p1, p2, ...).
    """
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        raise PdfOpenError(f"no se pudo abrir {pdf_path}: {exc!r}") from exc

    if doc.is_encrypted and not doc.authenticate(nit):
        doc.close()
        raise PdfOpenError(f"no se pudo autenticar {pdf_path} con el NIT provisto")

    OCR_CONFIG.images_dir.mkdir(parents=True, exist_ok=True)
    zoom = OCR_CONFIG.dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    paths: list[Path] = []
    for k in range(doc.page_count):
        page = doc.load_page(k)
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
        out_path = OCR_CONFIG.images_dir / f"{n:02d}_{cufe[:12]}_p{k + 1}.png"
        pix.save(str(out_path))
        paths.append(out_path)

    doc.close()
    return paths
