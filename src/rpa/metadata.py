"""Extracts the reference metadata shown on the DIAN detail page
(/Document/ShowDocumentToPublic) -- this is the ground truth later used to
measure OCR accuracy, so it's read straight from the rendered HTML, not OCR.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from patchright.sync_api import Page

from .config import CONFIG


def _match(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, text or "")
    return m.group(1).strip() if m else None


def extract_detail_metadata(page: Page, cufe: str) -> dict:
    doc_info_text = page.eval_on_selector(
        CONFIG.metadata_doc_info_selector,
        "el => { const p = el.closest('p'); return p ? p.innerText : ''; }",
    ) or ""

    party_texts = page.eval_on_selector_all(
        CONFIG.metadata_party_selector,
        "els => els.map(el => { const p = el.closest('p'); return p ? p.innerText : ''; })",
    )
    emisor_text = party_texts[0] if len(party_texts) > 0 else ""
    receptor_text = party_texts[1] if len(party_texts) > 1 else ""

    totals_el = page.query_selector(CONFIG.metadata_totals_selector)
    totals_text = totals_el.inner_text() if totals_el else ""

    return {
        "cufe": cufe,
        "serie": _match(r"Serie:\s*(\S+)", doc_info_text),
        "folio": _match(r"Folio:\s*(\S+)", doc_info_text),
        "fecha_emision": _match(r"Fecha de emisi[oó]n[^:]*:\s*(\S+)", doc_info_text),
        "emisor_nit": _match(r"NIT:\s*(\S+)", emisor_text),
        "emisor_nombre": _match(r"Nombre:\s*(.+)", emisor_text),
        "receptor_nit": _match(r"NIT:\s*(\S+)", receptor_text),
        "receptor_nombre": _match(r"Nombre:\s*(.+)", receptor_text),
        "total": _match(r"Total:\s*(\$?[\d.,]+)", totals_text),
    }


def load_metadata_store(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_metadata_entry(path: Path, cufe: str, entry: dict) -> None:
    store = load_metadata_store(path)
    store[cufe] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
