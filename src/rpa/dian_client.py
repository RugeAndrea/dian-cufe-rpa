"""End-to-end flow for a single CUFE: open search -> captcha -> submit ->
detail page -> capture PDF.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from patchright.sync_api import BrowserContext, Page

from .captcha import TurnstileBrowserSolver
from .config import CONFIG
from .downloader import download_pdf
from .metadata import extract_detail_metadata
from ..common.metrics import stage_timer

ERROR_PATTERNS = [
    "no corresponde",
    "no se encontr",
    "documento no encontrado",
    "no existe",
    "no fue posible",
]


class SearchError(Exception):
    """Raised for any failure before we have a valid detail page / PDF."""


@dataclass
class CufeResult:
    n: int
    cufe: str
    nit: str
    t_captcha: float = 0.0
    clic_checkbox: bool = False
    t_busqueda: float = 0.0
    t_descarga: float = 0.0
    t_total: float = 0.0
    intentos: int = 0
    metodo_descarga: Optional[str] = None
    endpoint_descarga: Optional[str] = None
    bytes: int = 0
    paginas: Optional[int] = None
    cifrado: Optional[bool] = None
    password_ok_con: Optional[str] = None
    tiene_capa_texto: Optional[bool] = None
    estado: str = "pendiente"
    error: Optional[str] = None


@dataclass
class ProcessResult:
    pdf_bytes: bytes
    metadata: dict


def _detect_page_error(page: Page) -> Optional[str]:
    try:
        text = page.inner_text("body")
    except Exception:
        return None
    lower = text.lower()
    for pattern in ERROR_PATTERNS:
        idx = lower.find(pattern)
        if idx != -1:
            return " ".join(text[max(0, idx - 40): idx + 120].split())
    return None


def process_cufe(context: BrowserContext, cufe: str, nit: str, result: CufeResult) -> ProcessResult:
    """Runs the full flow for one CUFE and returns the raw PDF bytes plus the
    reference metadata read from the detail page.

    Mutates `result` in place with per-stage timings as it goes, so partial
    progress is visible in the metrics even if a later stage raises.
    """
    page = context.new_page()
    try:
        page.goto(
            f"{CONFIG.base_url}{CONFIG.search_path}",
            wait_until="domcontentloaded",
            timeout=CONFIG.nav_timeout_ms,
        )

        captcha = TurnstileBrowserSolver().solve(page)
        result.t_captcha = captcha.seconds
        result.clic_checkbox = captcha.clicked_checkbox
        if not captcha.resolved:
            raise SearchError("captcha de busqueda no resuelto a tiempo")

        page.fill(CONFIG.document_key_selector, cufe)
        nit_input = page.query_selector(CONFIG.nit_selector)
        if nit_input is not None and nit_input.is_visible():
            nit_input.fill(nit)

        with stage_timer() as t_busqueda:
            page.get_by_role("button", name=CONFIG.search_button_name).click()
            try:
                page.wait_for_url(CONFIG.detail_url_pattern, timeout=CONFIG.search_result_timeout_ms)
            except Exception:
                error_text = _detect_page_error(page)
                raise SearchError(error_text or "no se navego a la pagina de detalle (timeout)")
        result.t_busqueda = t_busqueda["seconds"]

        error_text = _detect_page_error(page)
        if error_text:
            raise SearchError(error_text)

        metadata = extract_detail_metadata(page, cufe)

        # The download-specific Turnstile widget is already present on this
        # page; solve it now, BEFORE opening the download bootbox modal --
        # the modal intercepts pointer events over the widget once it's open
        # (see docs/spike_turnstile.md).
        download_captcha = TurnstileBrowserSolver().solve(page)
        if not download_captcha.resolved:
            raise SearchError("captcha de descarga no resuelto a tiempo")

        with stage_timer() as t_descarga:
            dl_result = download_pdf(page, cufe)
        result.t_descarga = t_descarga["seconds"]
        result.metodo_descarga = dl_result.method
        result.endpoint_descarga = dl_result.endpoint
        result.bytes = len(dl_result.data)
        result.error = None
        return ProcessResult(pdf_bytes=dl_result.data, metadata=metadata)
    finally:
        page.close()
