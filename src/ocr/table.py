"""Detects and parses the 'Detalles de Productos' table via a cell-based
approach: locate the region at native (300 DPI) resolution, re-render just
that region from the PDF at 600 DPI, find the actual bordered grid cells via
contour detection, map columns by exact-matching each header cell's text,
and OCR each item cell in isolation. See docs/ocr_tabla.md for the evidence
behind each design choice (confirmed on all 10 invoices: every row is
separated by a real horizontal grid line, which is what makes "one grid row
= one item" reliable instead of inferring item boundaries from word
positions).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import cv2
import fitz  # PyMuPDF
import numpy as np
from PIL import Image

from .config import OCR_CONFIG
from .engine import Word, image_to_text, image_to_words

SOURCE_DPI = 300  # dpi of the page-level PNGs used only to LOCATE the region
TABLE_DPI = 600  # dpi the table itself is re-rendered at, straight from the PDF

Cell = tuple[int, int, int, int]  # x, y, w, h (already shrunk to exclude borders)

HEADER_LABELS = {
    "nro": {"nro"},
    "codigo": {"codigo", "código"},
    "descripcion": {"descripcion", "descripción"},
    "cantidad": {"cantidad"},
    "precio_unitario": {"precio unitario"},
}


def _load_gray(image_path: Path) -> np.ndarray:
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"no se pudo leer la imagen {image_path}")
    return img


def _binarize(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15
    )


def _cluster_peaks(profile: np.ndarray, min_ratio: float = 0.4) -> list[int]:
    if profile.max() <= 0:
        return []
    threshold = profile.max() * min_ratio
    idx = np.where(profile > threshold)[0]
    if len(idx) == 0:
        return []
    clusters: list[list[int]] = [[idx[0]]]
    for x in idx[1:]:
        if x - clusters[-1][-1] <= 4:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [int(np.mean(c)) for c in clusters]


def detect_lines(gray: np.ndarray) -> tuple[list[int], list[int]]:
    """Returns (vertical_line_xs, horizontal_line_ys): confirmed grid-line
    positions, found via a column/row ink-sum profile (a true full-length
    grid line dominates the sum; an incidental character stroke never does,
    even though both can locally pass the morphological-opening test)."""
    h, w = gray.shape
    binary = _binarize(gray)

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 25, 10)))
    v_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel, iterations=2)

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 25, 10), 1))
    h_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel, iterations=2)

    v_xs = _cluster_peaks(v_mask.sum(axis=0))
    h_ys = _cluster_peaks(h_mask.sum(axis=1))
    return v_xs, h_ys


def _grid_mask(shape: tuple[int, int], v_xs: list[int], h_ys: list[int], thickness: int = 3) -> np.ndarray:
    """Thin rectangles at the CONFIRMED line positions only -- the raw
    morphological mask also matches tall character strokes (M/L/1)."""
    mask = np.zeros(shape, dtype=np.uint8)
    half = thickness // 2
    h, w = shape
    for x in v_xs:
        mask[:, max(0, x - half): min(w, x + half + 1)] = 255
    for y in h_ys:
        mask[max(0, y - half): min(h, y + half + 1), :] = 255
    return mask


def find_cell_grid(shape: tuple[int, int], v_xs: list[int], h_ys: list[int], margin: int = 4) -> list[list[Cell]]:
    """Finds actual table cells as the LEAF regions of the grid mask's
    contour hierarchy: a cell interior has no child contour, since nothing
    is nested inside an empty bordered box. Groups them into rows (by Y) and
    sorts each row left-to-right. Each box is shrunk by `margin` px per side
    so the border ink itself never lands inside an OCR crop."""
    mask = _grid_mask(shape, v_xs, h_ys)
    inverted = cv2.bitwise_not(mask)
    contours, hierarchy = cv2.findContours(inverted, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]
    crop_h, crop_w = shape

    boxes: list[Cell] = []
    for i, c in enumerate(contours):
        if hierarchy[i][2] != -1:
            continue
        x, y, w, h = cv2.boundingRect(c)
        if w < 15 or h < 15:
            continue
        # A real cell is bordered by an actual grid line on all four sides.
        # The render clip includes a sliver of blank page margin left of the
        # table and below its last row (the PDF clip rect spans the full
        # page width / the word-position-derived end_y, not the table's own
        # tight bbox) -- that margin has no internal lines, so it forms its
        # own leaf contour touching the crop edge. Verified on invoice 1:
        # dropping edge-touching boxes removes exactly that margin sliver
        # and the blank row below "Notas Finales", never a real column
        # (Nro./Codigo/Descripcion/Cantidad/Precio unitario always sit
        # strictly inside the grid in every invoice seen).
        touches_edge = x <= 1 or y <= 1 or x + w >= crop_w - 1 or y + h >= crop_h - 1
        if touches_edge:
            continue
        boxes.append((x + margin, y + margin, max(1, w - 2 * margin), max(1, h - 2 * margin)))

    boxes.sort(key=lambda b: b[1])
    rows: list[list[Cell]] = []
    for b in boxes:
        placed = False
        for row in rows:
            if abs(row[0][1] - b[1]) <= 10:
                row.append(b)
                placed = True
                break
        if not placed:
            rows.append([b])
    rows.sort(key=lambda row: row[0][1])
    for row in rows:
        row.sort(key=lambda b: b[0])
    return rows


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().strip(".:")).lower()


def _cell_text(gray: np.ndarray, cell: Cell, **kwargs) -> str:
    x, y, w, h = cell
    crop = gray[y:y + h, x:x + w]
    if crop.size == 0:
        return ""
    return image_to_text(Image.fromarray(crop), **kwargs).strip()


def map_header_row(gray: np.ndarray, rows: list[list[Cell]]) -> tuple[Optional[int], dict[str, int]]:
    """Finds the row containing a "Nro." cell and maps field -> column index
    by EXACT label match (normalized: trimmed, lowercased, trailing dots
    stripped). Cells are independent bordered boxes now, so "Precio
    unitario" and "Precio unitario de venta" can never be confused: they are
    different cells with different exact text ("precio unitario" vs
    "unitario de venta" -- the latter's cell spans up into the row above the
    one with "Precio", so its own cell text never contains the word
    "Precio" at all; verified on invoice 1's 600 DPI render)."""
    for ri, row in enumerate(rows):
        texts = [_normalize_label(_cell_text(gray, cell, psm=6)) for cell in row]
        if "nro" not in texts:
            continue
        mapping: dict[str, int] = {}
        for ci, norm in enumerate(texts):
            for field, labels in HEADER_LABELS.items():
                if norm in labels:
                    mapping[field] = ci
        return ri, mapping
    return None, {}


def render_table_high_res(
    pdf_path: Path, nit: str, n: int, cufe: str, start_y_px: int, end_y_px: int, page_width_px: int
) -> tuple[Path, np.ndarray]:
    """Re-renders the already-located table region straight from the PDF at
    TABLE_DPI instead of upscaling the 300 DPI page PNG: native 600 DPI ink
    is measurably cleaner than 2x-interpolated 300 DPI ink. Pixel
    coordinates from the 300 DPI locator image are converted to PDF points
    (px * 72 / 300) to build the clip rect."""
    doc = fitz.open(str(pdf_path))
    try:
        if doc.is_encrypted and not doc.authenticate(nit):
            raise ValueError(f"no se pudo autenticar {pdf_path} con el NIT provisto")
        page = doc.load_page(0)
        scale = 72.0 / SOURCE_DPI
        rect = fitz.Rect(0, start_y_px * scale, page_width_px * scale, end_y_px * scale)
        pix = page.get_pixmap(dpi=TABLE_DPI, clip=rect, colorspace=fitz.csGRAY)
        OCR_CONFIG.images_dir.mkdir(parents=True, exist_ok=True)
        out_path = OCR_CONFIG.images_dir / f"{n:02d}_{cufe[:12]}_tabla_p1.png"
        pix.save(str(out_path))
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
        return out_path, gray
    finally:
        doc.close()


def locate_table_region(words: list[Word], page_height: int) -> Optional[tuple[int, int]]:
    start_word = next((w for w in words if w.text.strip(".:") == OCR_CONFIG.table_start_marker), None)
    if start_word is None:
        return None
    start_y = start_word.top

    end_first_words = {m.split()[0] for m in OCR_CONFIG.table_end_markers}
    end_candidates = [
        w.top for w in words if w.top > start_y + 20 and w.text.strip(".:") in end_first_words
    ]
    end_y = min(end_candidates) if end_candidates else page_height
    return start_y, end_y


def _is_blank_row(gray: np.ndarray, row: list[Cell], ink_threshold: int = 30) -> bool:
    """Cheap ink-density check (no OCR) to drop stray trailing contour rows
    past the last real item."""
    for (x, y, w, h) in row:
        crop = gray[y:y + h, x:x + w]
        if crop.size == 0:
            continue
        if (_binarize(crop) > 0).sum() > ink_threshold:
            return False
    return True


def extract_table(
    image_paths: list[Path],
    pdf_path: Path,
    nit: str,
    n: int,
    cufe: str,
    debug_dir: Optional[Path] = None,
    debug_prefix: str = "",
) -> dict:
    """Convenience wrapper: runs structure detection + item OCR in one call."""
    structure = detect_table_structure(
        image_paths, pdf_path, nit, n, cufe, debug_dir=debug_dir, debug_prefix=debug_prefix
    )
    if not structure["found"]:
        return {"found": False, "items": []}
    items = ocr_table_items(structure)
    return {"found": True, "items": items, "continues_next_page": structure["continues_next_page"]}


def detect_table_structure(
    image_paths: list[Path],
    pdf_path: Path,
    nit: str,
    n: int,
    cufe: str,
    debug_dir: Optional[Path] = None,
    debug_prefix: str = "",
) -> dict:
    """Locates the table region on the page PNG, re-renders it from the PDF
    at 600 DPI, and finds its cell grid + column mapping. Item OCR happens
    in ocr_table_items, timed separately by the pipeline."""
    if not image_paths:
        return {"found": False}

    page_gray = _load_gray(image_paths[0])
    locator_words = image_to_words(image_paths[0], psm=OCR_CONFIG.psm_header)
    region = locate_table_region(locator_words, page_gray.shape[0])
    if region is None:
        return {"found": False}

    start_y, end_y = region
    found_end_marker = end_y < page_gray.shape[0]
    page_width = page_gray.shape[1]

    hi_res_path, hi_res_gray = render_table_high_res(pdf_path, nit, n, cufe, start_y, end_y, page_width)

    v_xs, h_ys = detect_lines(hi_res_gray)
    rows = find_cell_grid(hi_res_gray.shape, v_xs, h_ys)

    header_idx, columns = map_header_row(hi_res_gray, rows)
    if header_idx is None:
        return {"found": False}

    item_rows = [row for row in rows[header_idx + 1:] if not _is_blank_row(hi_res_gray, row)]

    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(debug_dir / f"{debug_prefix}tabla_600dpi.png"), hi_res_gray)
        cv2.imwrite(str(debug_dir / f"{debug_prefix}grid_mask.png"), _grid_mask(hi_res_gray.shape, v_xs, h_ys))
        preview = cv2.cvtColor(hi_res_gray, cv2.COLOR_GRAY2BGR)
        for ri, row in enumerate(rows):
            color = (0, 0, 255) if ri == header_idx else (0, 180, 0)
            for (x, y, w, h) in row:
                cv2.rectangle(preview, (x, y), (x + w, y + h), color, 2)
        for field, ci in columns.items():
            if ci < len(rows[header_idx]):
                x, y, _, _ = rows[header_idx][ci]
                cv2.putText(preview, field, (x + 2, max(20, y + 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
        cv2.imwrite(str(debug_dir / f"{debug_prefix}celdas.png"), preview)

    return {
        "found": True,
        "hi_res_gray": hi_res_gray,
        "rows": item_rows,
        "columns": columns,
        "continues_next_page": not found_end_marker,
    }


def _field_confidence(words: list[Word]) -> Optional[float]:
    confs = [w.conf for w in words]
    return sum(confs) / len(confs) if confs else None


def _crop_cell(gray: np.ndarray, cell: Cell, border: int = 3) -> np.ndarray:
    """Crops the cell and whitens a thin outer border. The `margin` shrink
    in find_cell_grid is geometric (based on the detected line position) and
    occasionally leaves a faint anti-aliased line remnant along one edge,
    which Tesseract can read as a spurious '.' or '-' (verified on invoice
    1's Codigo cell: a single "0" came back as "0.0" until this was added)."""
    x, y, w, h = cell
    crop = gray[y:y + h, x:x + w].copy()
    if crop.size == 0:
        return crop
    crop[:border, :] = 255
    crop[-border:, :] = 255
    crop[:, :border] = 255
    crop[:, -border:] = 255
    return crop


def _ocr_text_cell(gray: np.ndarray, cell: Cell, whitelist: Optional[str] = None) -> tuple[str, Optional[float]]:
    """Codigo/Descripcion cells: --oem 1 --psm 6, preserve_interword_spaces.
    Codigo additionally gets a whitelist (DIAN product codes are uppercase
    alphanumeric) -- this eliminates by construction the stray accents and
    duplicated letters Tesseract otherwise hallucinates into a short
    alphanumeric token (verified: "SPON1"->"SPONÍ1", "C40112"->"Cc40112").

    Text comes from image_to_string, NOT image_to_data/image_to_words:
    verified on invoice 1's Codigo cell (a lone "0") that the two give
    different results for the EXACT same image and config string --
    image_to_data's TSV path read "0.0", image_to_string read "0" cleanly.
    image_to_words is still used, separately, purely for the confidence
    score (its own text is discarded).
    """
    crop = _crop_cell(gray, cell)
    if crop.size == 0:
        return "", None
    pil = Image.fromarray(crop)
    extra = "-c preserve_interword_spaces=1"
    if whitelist:
        extra += f' -c tessedit_char_whitelist="{whitelist}"'
    raw = image_to_text(pil, psm=6, oem=1, extra_config=extra)
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    # Join physical lines with NO separator -- data-driven rule (see
    # docs/ocr_tabla.md): the DIAN generator wraps this column by raw
    # character width, cutting mid-word in 22 of 23 observed cases.
    text = "".join(lines)
    words = image_to_words(pil, psm=6, oem=1, whitelist=whitelist, extra_config="-c preserve_interword_spaces=1")
    return text, _field_confidence(words)


def _ocr_numeric_cell(gray: np.ndarray, cell: Cell) -> tuple[str, Optional[float]]:
    """Cantidad/Precio unitario cells: --psm 7 (single line) + digit/currency
    whitelist. Same image_to_string/image_to_words split as _ocr_text_cell."""
    crop = _crop_cell(gray, cell)
    if crop.size == 0:
        return "", None
    pil = Image.fromarray(crop)
    text = image_to_text(pil, psm=7, extra_config=f'-c tessedit_char_whitelist="{OCR_CONFIG.numeric_whitelist}"').strip()
    words = image_to_words(pil, psm=7, whitelist=OCR_CONFIG.numeric_whitelist)
    return text, _field_confidence(words)


def ocr_table_items(structure: dict) -> list[dict]:
    if not structure.get("found"):
        return []

    gray = structure["hi_res_gray"]
    columns = structure["columns"]

    items = []
    for row in structure["rows"]:
        codigo_raw = descripcion_raw = cantidad_raw = precio_raw = ""
        confs: dict[str, Optional[float]] = {}

        if "codigo" in columns and columns["codigo"] < len(row):
            codigo_raw, confs["codigo"] = _ocr_text_cell(
                gray, row[columns["codigo"]], whitelist=OCR_CONFIG.codigo_whitelist
            )
        if "descripcion" in columns and columns["descripcion"] < len(row):
            descripcion_raw, confs["descripcion"] = _ocr_text_cell(gray, row[columns["descripcion"]])
        if "cantidad" in columns and columns["cantidad"] < len(row):
            cantidad_raw, confs["cantidad"] = _ocr_numeric_cell(gray, row[columns["cantidad"]])
        if "precio_unitario" in columns and columns["precio_unitario"] < len(row):
            precio_raw, confs["precio_unitario"] = _ocr_numeric_cell(gray, row[columns["precio_unitario"]])

        field_confs = [c for c in confs.values() if c is not None]
        items.append(
            {
                "codigo_raw": codigo_raw,
                "descripcion_raw": descripcion_raw,
                "cantidad_raw": cantidad_raw,
                "precio_unitario_raw": precio_raw,
                "confianza_min": min(field_confs) if field_confs else None,
            }
        )
    return items
