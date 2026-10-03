"""Detects and parses the 'Detalles de Productos' table via OpenCV grid
detection + column-aware OCR. See docs/ocr_tabla.md for the full design
rationale and the pitfalls (header row disambiguation, grid-line removal,
multi-line descriptions) this code works around.
"""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from .config import OCR_CONFIG
from .engine import Word, image_to_words

ITEM_START_RE = re.compile(r"^\d+$")


def _split_nro_codigo_merge(words: list[Word], columns: dict[str, tuple[int, int]]) -> list[Word]:
    """Splits a word whose box straddles the Nro./Codigo boundary with a
    digit-item-number glued onto the following code (verified on invoice 6:
    the body pass read item 1's "1" and codigo "88143" as one token,
    "188143", spanning both columns -- its center then fell inside the
    Codigo interval, so the Nro. column never saw a digit and the item's
    anchor was missed entirely).

    Only the single leading digit is split off as the item number: every
    invoice in this batch has single-digit item numbers (1-9), and the
    split point is NOT proportional to character count (that overcounts --
    "188143" splits 1/6 of the characters but ~17% of the pixel width, since
    "1" is narrower than the other digits), so we always cut after exactly
    one character and let the box widths fall wherever the glyphs actually
    are.
    """
    if "nro" not in columns or "codigo" not in columns:
        return words

    _, nro_hi = columns["nro"]
    result: list[Word] = []
    for w in words:
        if w.left < nro_hi < w.right and len(w.text) >= 2 and w.text[0].isdigit():
            cut = nro_hi
            result.append(replace(w, text=w.text[0], width=cut - w.left))
            result.append(replace(w, text=w.text[1:], left=cut, width=w.right - cut))
        else:
            result.append(w)
    return result


def _load_gray(image_path: Path) -> np.ndarray:
    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"no se pudo leer la imagen {image_path}")
    return img


def _binarize(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15
    )


def detect_lines(gray: np.ndarray) -> tuple[list[int], list[int], np.ndarray]:
    """Returns (vertical_line_xs, horizontal_line_ys, combined_line_mask)."""
    h, w = gray.shape
    binary = _binarize(gray)

    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 25, 10)))
    v_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel, iterations=2)

    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 25, 10), 1))
    h_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel, iterations=2)

    v_xs = _cluster_peaks(v_mask.sum(axis=0))
    h_ys = _cluster_peaks(h_mask.sum(axis=1))

    combined = cv2.bitwise_or(v_mask, h_mask)
    return v_xs, h_ys, combined


def build_line_removal_mask(
    shape: tuple[int, int], v_xs: list[int], h_ys: list[int], thickness: int = 5
) -> np.ndarray:
    """Draws thin rectangles at the CONFIRMED line positions only (not the
    raw morphological mask, which also matches tall character strokes like
    M/L/1 and would erase text instead of just the grid -- verified
    empirically, see docs/ocr_tabla.md)."""
    mask = np.zeros(shape, dtype=np.uint8)
    half = thickness // 2
    h, w = shape
    for x in v_xs:
        mask[:, max(0, x - half): min(w, x + half + 1)] = 255
    for y in h_ys:
        mask[max(0, y - half): min(h, y + half + 1), :] = 255
    return mask


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


def find_header_row(words: list[Word]) -> tuple[Optional[Word], list[Word]]:
    """Finds the "Nro. | Codigo | Descripcion | ..." header row.

    Filters by actual Y-proximity to the "Nro." word, NOT by Tesseract's own
    line_num/block_num: verified on invoice 5, where Tesseract's psm-4 layout
    analysis merged the header labels (top~2096-2103) and the first data
    row's values (top~2233-2258, ~130px below) into the SAME line_num/block_num.
    That silently interleaved a data value ("663.203,00") between the
    "Precio" and "unitario" header tokens in left-to-right order, breaking
    the adjacent-pair match in map_columns, and pushed header_bottom (derived
    from max(bottom) of "header_words") deep into the row, cutting off most
    of the body before OCR ever saw it.
    """
    nro_word = next((w for w in words if w.text.strip(".").lower() == "nro"), None)
    if nro_word is None:
        return None, []
    tolerance = max(nro_word.height * 1.5, 15)
    header_words = [
        w
        for w in words
        if abs(w.top - nro_word.top) <= tolerance
        # Stray single-character OCR noise (table-border artifacts read as
        # ':', '|', etc.) sits at the same Y as the real header text and
        # would otherwise land right between "Precio" and "unitario" in
        # left-to-right order, breaking their adjacent-pair match below.
        and not re.fullmatch(r"[|:;,.\-_]+", w.text)
    ]
    return nro_word, header_words


def map_columns(header_words: list[Word], v_xs: list[int]) -> dict[str, tuple[int, int]]:
    boundaries = sorted(set(v_xs))
    intervals = list(zip(boundaries[:-1], boundaries[1:]))

    def interval_for(x: float) -> Optional[tuple[int, int]]:
        for lo, hi in intervals:
            if lo <= x <= hi:
                return (lo, hi)
        return None

    mapping: dict[str, tuple[int, int]] = {}
    sorted_words = sorted(header_words, key=lambda w: w.left)
    for i, w in enumerate(sorted_words):
        norm = w.text.strip(".:").lower()
        if norm == "nro":
            iv = interval_for(w.center_x)
            if iv:
                mapping["nro"] = iv
        elif norm.startswith("codigo") or norm.startswith("código"):
            iv = interval_for(w.center_x)
            if iv:
                mapping["codigo"] = iv
        elif norm.startswith("descripci"):
            iv = interval_for(w.center_x)
            if iv:
                mapping["descripcion"] = iv
        elif norm.startswith("cantidad"):
            iv = interval_for(w.center_x)
            if iv:
                mapping["cantidad"] = iv
        elif norm == "precio":
            # Search forward for the nearest "unitario" token instead of
            # requiring exact index+1 adjacency: invoices occasionally carry
            # a stray single-character OCR artifact (a border pixel misread
            # as ':', '7', etc.) sitting right between "Precio" and
            # "unitario" at the same Y, which would otherwise break a naive
            # adjacency check (verified on invoices 1, 5 and 8 -- a
            # different stray character each time, so matching by distance
            # instead of position is what generalizes).
            nxt = None
            for cand in sorted_words[i + 1:]:
                if cand.left - w.right > 150:
                    break
                if cand.text.strip(".:").lower().startswith("unitario"):
                    nxt = cand
                    break
            if nxt is not None:
                nxt_idx = sorted_words.index(nxt)
                nxt2 = sorted_words[nxt_idx + 1] if nxt_idx + 1 < len(sorted_words) else None
                is_de_venta = (
                    nxt2 is not None
                    and nxt2.text.strip(".:").lower() in ("de", "venta")
                    and nxt2.left - nxt.right < 60
                )
                if not is_de_venta:
                    iv_lo = interval_for(w.center_x)
                    iv_hi = interval_for(nxt.center_x)
                    if iv_lo and iv_hi:
                        mapping["precio_unitario"] = (iv_lo[0], iv_hi[1])
                    elif iv_lo:
                        mapping["precio_unitario"] = iv_lo
    return mapping


def _assign_column(word: Word, columns: dict[str, tuple[int, int]]) -> Optional[str]:
    cx = word.center_x
    for name, (lo, hi) in columns.items():
        if lo <= cx <= hi:
            return name
    return None


def _reocr_cell(
    gray_full: np.ndarray, y0: int, y1: int, x0: int, x1: int
) -> tuple[str, Optional[float]]:
    """Re-OCRs a Cantidad/Precio unitario cell in isolation: 10px white
    margin, upscaled 3x (INTER_CUBIC), digit/currency whitelist. The bigger
    crop + bigger upscale measurably raised confidence above the 80
    threshold versus the body-pass OCR (which reads the whole row at once
    at native resolution)."""
    pad = 10
    cell = gray_full[max(0, y0 - pad): y1 + pad, max(0, x0 - pad): x1 + pad].copy()
    if cell.size == 0:
        return "", None
    # Blank out a thin border instead of re-running line detection on a tiny
    # crop (too small for the morphology thresholds to tell a grid line from
    # a character stroke) -- the pad already isolates any adjacent gridline
    # to just these edge pixels.
    edge = 6
    cell[:edge, :] = 255
    cell[-edge:, :] = 255
    cell[:, :edge] = 255
    cell[:, -edge:] = 255
    scale = 3
    cell = cv2.resize(cell, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    words = image_to_words(
        Image.fromarray(cell), psm=7, whitelist=OCR_CONFIG.numeric_whitelist
    )
    text = " ".join(w.text for w in words)
    confs = [w.conf for w in words]
    return text.strip(), (min(confs) if confs else None)


def _reocr_descripcion_cell(
    gray_full: np.ndarray, y0: int, y1: int, x0: int, x1: int
) -> tuple[str, Optional[float]]:
    """Re-OCRs the Descripcion cell in isolation: 15px white margin, upscaled
    2x (INTER_CUBIC), --psm 6, preserve_interword_spaces=1 -- this is what
    correctly keeps the "L" / "O" of a mid-word line break as two distinct
    tokens (the whole-row body pass merged them into "LO").

    Lines within the crop are re-joined WITHOUT a separator between them,
    never with a space. That rule comes from measuring the actual PDF text
    layer of all 10 invoices (see docs/ocr_tabla.md): the DIAN generator
    wraps this column by raw character width, not by word -- of 23 wrapped
    descriptions, 22 cut strictly mid-word (e.g. "UNILATERA"+"L"). The one
    counter-example ("...RAPIDA VIH" + "1 Y 2...") only differs by an
    invisible trailing space present in the PDF's raw character stream
    (confirmed via page.get_text("rawdict")) -- but that space has zero
    visible ink and a measured width (~2pt) indistinguishable from ordinary
    kerning, and the geometric "distance to the column's right margin" for
    that line (4.46pt) is statistically identical to confirmed mid-word cuts
    in the same invoice (e.g. 4.44pt for "CPN HEMOGRAMA I (HEM"+"OGLOBINA").
    In other words: this information is only recoverable from the PDF's
    internal text stream, never from the rendered image, so no OCR-visible
    signal can recover it. Always joining without a space is therefore the
    rule the data actually supports (correct in ~96% of observed cases,
    22/23), not an assumption.
    """
    pad = 15
    cell = gray_full[max(0, y0 - pad): y1 + pad, max(0, x0 - pad): x1 + pad].copy()
    if cell.size == 0:
        return "", None
    edge = 6
    cell[:edge, :] = 255
    cell[-edge:, :] = 255
    cell[:, :edge] = 255
    cell[:, -edge:] = 255
    scale = 2
    cell = cv2.resize(cell, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    words = image_to_words(
        Image.fromarray(cell), psm=6, extra_config="-c preserve_interword_spaces=1"
    )
    if not words:
        return "", None

    confs = [w.conf for w in words]
    components = _connected_components(cell)
    lines = _cluster_by_y(words, gap=int(10 * scale))

    text_lines = []
    for line_words in lines:
        tokens, extra_confs = _split_merged_tokens(cell, sorted(line_words, key=lambda w: w.left), components)
        confs.extend(extra_confs)
        text_lines.append(" ".join(tokens))

    text = "".join(text_lines).strip()
    return text, (min(confs) if confs else None)


def _connected_components(binary_source: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    _, binary = cv2.threshold(binary_source, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    return [tuple(stats[i]) for i in range(1, n) if stats[i][4] > 3]


def _split_merged_tokens(
    cell: np.ndarray, line_words: list[Word], components: list[tuple[int, int, int, int, int]]
) -> tuple[list[str], list[float]]:
    """Tesseract's own word-grouping occasionally merges two short adjacent
    words (verified on invoice 1: "L" + "O" -> "LO") despite a gap as wide as
    ones it correctly treats as word boundaries elsewhere on the SAME line.
    Fix: re-measure gaps between ink blobs (connected components, independent
    of Tesseract's heuristic) inside each recognized word's bbox; if an
    internal gap is at least as wide as the smallest gap Tesseract itself
    already used to split two *different* words on this line, split there
    too -- re-OCRing each half in isolation (psm 8) rather than guessing how
    to slice the merged string, since character count and glyph count don't
    always match (ligatures, accents).

    Calibration data (3x scale, invoice 1, line "L O PIEZA QUIRURGICA"):
    component gaps were 21 (L-O), 33 (O-PIEZA), 5-9 (within PIEZA), 28
    (PIEZA-QUIRURGICA), 3-9 (within QUIRURGICA) -- i.e. the merged "LO"'s
    internal 21px gap sits inside the 21-33px band of CONFIRMED inter-word
    gaps on that line, cleanly separated from the 3-9px intra-word band.
    """
    extra_confs: list[float] = []
    if len(line_words) > 1:
        confirmed_gaps = [
            line_words[i + 1].left - line_words[i].right for i in range(len(line_words) - 1)
        ]
        threshold = min(confirmed_gaps) * 0.6 if confirmed_gaps else None
    else:
        threshold = None

    tokens: list[str] = []
    for w in line_words:
        if threshold is None:
            tokens.append(w.text)
            continue

        inner = sorted(
            (
                c
                for c in components
                if w.left - 2 <= c[0]
                and c[0] + c[2] <= w.right + 2
                and abs((c[1] + c[3] / 2) - w.center_y) <= w.height * 0.75
            ),
            key=lambda c: c[0],
        )
        split_at = [
            i + 1
            for i in range(len(inner) - 1)
            if inner[i + 1][0] - (inner[i][0] + inner[i][2]) >= threshold
        ]
        if not split_at or len(inner) < 2:
            tokens.append(w.text)
            continue

        bounds = [0] + split_at + [len(inner)]
        for a, b in zip(bounds, bounds[1:]):
            piece = inner[a:b]
            margin = 10
            px0 = max(0, min(c[0] for c in piece) - margin)
            px1 = max(c[0] + c[2] for c in piece) + margin
            py0 = max(0, min(c[1] for c in piece) - margin)
            py1 = max(c[1] + c[3] for c in piece) + margin
            crop = cell[py0:py1, px0:px1]
            crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            # psm 6 beat 7/8/10/13 empirically on an isolated single-glyph
            # crop like this ("O" alone): the single-char/single-word modes
            # misread it as digits/noise ("0.0", "10]"), while psm 6 (treat
            # as a uniform block of text) read it correctly regardless of
            # lang=spa/eng.
            sub_words = image_to_words(Image.fromarray(crop), psm=6)
            piece_text = "".join(sw.text for sw in sub_words)
            tokens.append(piece_text if piece_text else w.text)
            extra_confs.extend(sw.conf for sw in sub_words)

    return tokens, extra_confs


def extract_table(
    image_paths: list[Path], debug_dir: Optional[Path] = None, debug_prefix: str = ""
) -> dict:
    """Convenience wrapper: runs structure detection + body OCR in one call.
    Kept for tests/ad-hoc use; the pipeline calls the two phases separately
    so it can time them independently (deteccion_tabla vs ocr_tabla).
    """
    structure = detect_table_structure(image_paths, debug_dir=debug_dir, debug_prefix=debug_prefix)
    if not structure["found"]:
        return {"found": False, "items": []}
    items = ocr_table_items(structure)
    return {
        "found": True,
        "items": items,
        "continues_next_page": structure["continues_next_page"],
    }


def detect_table_structure(
    image_paths: list[Path], debug_dir: Optional[Path] = None, debug_prefix: str = ""
) -> dict:
    """Locates the table region and column boundaries, and prepares the
    grid-free body crop for OCR, WITHOUT running that OCR yet (that's
    ocr_table_items's job, timed separately in the pipeline).
    """
    if not image_paths:
        return {"found": False}

    gray1 = _load_gray(image_paths[0])
    locator_words = image_to_words(image_paths[0], psm=OCR_CONFIG.psm_header)
    region = locate_table_region(locator_words, gray1.shape[0])
    if region is None:
        return {"found": False}

    start_y, end_y = region
    found_end_marker = end_y < gray1.shape[0]
    pad = 8
    crop_start = max(0, start_y - pad)
    crop_end = min(gray1.shape[0], end_y + pad)
    table_crop = gray1[crop_start:crop_end, :]

    v_xs, h_ys, _ = detect_lines(table_crop)
    line_mask = build_line_removal_mask(table_crop.shape, v_xs, h_ys)

    header_region_words = [w for w in locator_words if start_y - pad <= w.top <= start_y + 300]
    nro_word, header_words = find_header_row(header_region_words)
    if nro_word is None:
        return {"found": False}

    columns = map_columns(header_words, v_xs)
    header_bottom = max(w.bottom for w in header_words) + 4
    body_local_top = header_bottom - crop_start

    body_crop = table_crop[body_local_top:, :]
    body_mask = line_mask[body_local_top:, :]
    body_clean = body_crop.copy()
    body_clean[body_mask > 0] = 255

    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(debug_dir / f"{debug_prefix}table_crop.png"), table_crop)
        cv2.imwrite(str(debug_dir / f"{debug_prefix}line_mask.png"), line_mask)
        cv2.imwrite(str(debug_dir / f"{debug_prefix}body_clean.png"), body_clean)
        columns_img = cv2.cvtColor(table_crop, cv2.COLOR_GRAY2BGR)
        for name, (lo, hi) in columns.items():
            cv2.rectangle(columns_img, (lo, 0), (hi, table_crop.shape[0]), (0, 0, 255), 2)
            cv2.putText(columns_img, name, (lo + 2, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
        cv2.imwrite(str(debug_dir / f"{debug_prefix}columns.png"), columns_img)

    return {
        "found": True,
        "gray1": gray1,
        "columns": columns,
        "header_bottom": header_bottom,
        "body_clean": body_clean,
        "continues_next_page": not found_end_marker,
    }


def ocr_table_items(structure: dict) -> list[dict]:
    if not structure.get("found"):
        return []

    gray1 = structure["gray1"]
    columns = structure["columns"]
    header_bottom = structure["header_bottom"]

    body_words = image_to_words(Image.fromarray(structure["body_clean"]), psm=OCR_CONFIG.psm_table_body)
    body_words = _split_nro_codigo_merge(body_words, columns)
    items = _words_to_items(body_words, columns)

    for item in items:
        if item.get("_descripcion_box"):
            y0, y1, x0, x1 = item["_descripcion_box"]
            text, conf = _reocr_descripcion_cell(gray1, header_bottom + y0, header_bottom + y1, x0, x1)
            if text:
                item["descripcion_raw"] = text
                item["_confs"].append(conf if conf is not None else 0.0)
        if item.get("_cantidad_box"):
            y0, y1, x0, x1 = item["_cantidad_box"]
            text, conf = _reocr_cell(gray1, header_bottom + y0, header_bottom + y1, x0, x1)
            if text:
                item["cantidad_raw"] = text
                item["_confs"].append(conf if conf is not None else 0.0)
        if item.get("_precio_box"):
            y0, y1, x0, x1 = item["_precio_box"]
            text, conf = _reocr_cell(gray1, header_bottom + y0, header_bottom + y1, x0, x1)
            if text:
                item["precio_unitario_raw"] = text
                item["_confs"].append(conf if conf is not None else 0.0)

    for item in items:
        item["confianza_min"] = min(item["_confs"]) if item["_confs"] else None
        del item["_confs"]
        item.pop("_descripcion_box", None)
        item.pop("_cantidad_box", None)
        item.pop("_precio_box", None)

    return items


def _cluster_by_y(words: list[Word], gap: int = 10) -> list[list[Word]]:
    """Groups words into physical text lines by y-proximity. Needed because
    a multi-line Descripcion cell is vertically centered on its row, so its
    first line sits ABOVE the row's single-line cells (Cantidad, Precio...)
    and its second line sits BELOW them -- Tesseract's own line_num follows
    its internal block order, not top-to-bottom pixel position, so we redo
    the clustering ourselves from actual y coordinates."""
    if not words:
        return []
    ws = sorted(words, key=lambda w: w.top)
    lines = [[ws[0]]]
    for w in ws[1:]:
        if w.top - lines[-1][-1].top <= gap:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines


def _words_to_items(body_words: list[Word], columns: dict[str, tuple[int, int]]) -> list[dict]:
    """Groups words into items anchored on the 'Nro.' column.

    Rows are first grouped into PHYSICAL LINES (by y-proximity, across all
    columns), then walked top-to-bottom. A line is an "anchor line" if it
    carries a pure-digit token in the Nro. column -- that's the single line
    per item holding Nro./Codigo/Cantidad/Precio together.

    An earlier version assigned every word to whichever anchor's Y was
    numerically closest (splitting at the midpoint between consecutive
    anchors). That breaks for uneven row heights: verified on invoice 4,
    where a short 3-line item is immediately followed by a tall 6-line item
    -- the midpoint fell BELOW the tall item's first Descripcion line (which,
    like invoice 1's "MAMOGRAFIA UNILATERA", sits above its own anchor),
    merging it into the wrong (preceding) item.

    The actual pattern in the data (invoices 1 and 4): at most ONE orphan
    line sits ABOVE its own anchor; every other orphan line between two
    anchors is a trailing continuation of the PRECEDING item. So: walking
    lines in order, only the LAST pending orphan before a new anchor is
    handed to the upcoming item; everything else pending goes to the item
    still in progress.
    """
    lines = _cluster_by_y(body_words)

    def is_anchor(line_words: list[Word]) -> bool:
        return any(
            _assign_column(w, columns) == "nro" and ITEM_START_RE.match(w.text.strip("."))
            for w in line_words
        )

    buckets: list[list[Word]] = []
    pending: list[list[Word]] = []
    current: Optional[list[Word]] = None

    for line_words in lines:
        if is_anchor(line_words):
            if current is None:
                # No preceding item exists, so there's nowhere else these
                # orphans could belong -- ALL of them lead into this first
                # item (verified on invoice 8: a 4-line description centers
                # its anchor so that TWO lines, not just one, sit above it).
                new_item_words = [w for orphan_line in pending for w in orphan_line]
            else:
                for orphan_line in pending[:-1]:
                    current.extend(orphan_line)
                new_item_words = list(pending[-1]) if pending else []
            new_item_words.extend(line_words)
            buckets.append(new_item_words)
            current = buckets[-1]
            pending = []
        else:
            pending.append(line_words)

    if current is not None:
        for orphan_line in pending:
            current.extend(orphan_line)

    results = []
    for bucket in buckets:
        by_col: dict[str, list[Word]] = {}
        for w in bucket:
            col = _assign_column(w, columns)
            if col:
                by_col.setdefault(col, []).append(w)

        # Confidence here covers only codigo: descripcion, cantidad and
        # precio_unitario are all about to be superseded by a dedicated
        # whitelist/preserve-spaces re-OCR of their own cell (see
        # ocr_table_items), so their body-pass confidence (often polluted by
        # stray punctuation the grid-removal leaves behind, or by merged
        # tokens) must not count towards confianza_min.
        confs = [w.conf for w in by_col.get("codigo", [])]

        codigo = " ".join(w.text for w in sorted(by_col.get("codigo", []), key=lambda w: w.left)).strip()

        desc_words = by_col.get("descripcion", [])
        desc_lines = _cluster_by_y(desc_words)
        descripcion = "".join(
            " ".join(w.text for w in sorted(line, key=lambda w: w.left)) for line in desc_lines
        ).strip()
        descripcion_box = _expand_box(None, desc_words, columns["descripcion"]) if desc_words else None

        cantidad_words = by_col.get("cantidad", [])
        cantidad_raw = " ".join(w.text for w in sorted(cantidad_words, key=lambda w: w.left)).strip()
        cantidad_box = _expand_box(None, cantidad_words, columns["cantidad"]) if cantidad_words else None

        precio_words = by_col.get("precio_unitario", [])
        precio_raw = " ".join(w.text for w in sorted(precio_words, key=lambda w: w.left)).strip()
        precio_box = _expand_box(None, precio_words, columns["precio_unitario"]) if precio_words else None

        results.append(
            {
                "codigo_raw": codigo,
                "descripcion_raw": descripcion,
                "cantidad_raw": cantidad_raw,
                "precio_unitario_raw": precio_raw,
                "_confs": confs,
                "_descripcion_box": descripcion_box,
                "_cantidad_box": cantidad_box,
                "_precio_box": precio_box,
            }
        )
    return results


def _expand_box(existing, words: list[Word], column_interval: tuple[int, int]):
    tops = [w.top for w in words]
    bottoms = [w.bottom for w in words]
    y0, y1 = min(tops), max(bottoms)
    x0, x1 = column_interval
    if existing is None:
        return (y0, y1, x0, x1)
    ey0, ey1, ex0, ex1 = existing
    return (min(ey0, y0), max(ey1, y1), x0, x1)
