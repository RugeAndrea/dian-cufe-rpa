"""Centralized configuration: URLs, selectors, timeouts and paths.

Every value here can be overridden via environment variables (see .env.example).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_path(name: str, default: str) -> Path:
    raw = _env(name, default)
    path = Path(raw)
    return path if path.is_absolute() else BASE_DIR / path


@dataclass(frozen=True)
class Config:
    # --- DIAN URLs ---
    base_url: str = _env("DIAN_BASE_URL", "https://catalogo-vpfe.dian.gov.co")
    search_path: str = _env("DIAN_SEARCH_PATH", "/User/SearchDocument")
    detail_url_pattern: str = _env("DIAN_DETAIL_URL_PATTERN", "**/Document/ShowDocumentToPublic**")
    download_endpoint_substr: str = _env("DIAN_DOWNLOAD_ENDPOINT", "/Document/DownloadPDF")

    # --- Selectors (verified against the real site, see docs/spike_turnstile.md) ---
    form_selector: str = "#search-document-form"
    document_key_selector: str = 'input[name="DocumentKey"]'
    nit_selector: str = 'input[name="SearchDocumentNit"]'
    search_button_name: str = "Buscar"
    turnstile_response_selector: str = 'input[name="cf-turnstile-response"]'
    download_link_selector: str = "a.downloadLink"
    bootbox_visible_selector: str = ".bootbox:has-text('Aceptar')"
    bootbox_accept_selector: str = ".bootbox button:has-text('Aceptar')"

    # --- Detail-page reference metadata selectors ---
    metadata_doc_info_selector: str = ".tipo-doc"
    metadata_party_selector: str = ".datos-receptor"
    metadata_totals_selector: str = ".col-md-4:has-text('TOTALES E IMPUESTOS')"

    # --- Browser ---
    chrome_channel: str = _env("CHROME_CHANNEL", "chrome")
    headless: bool = _env_bool("HEADLESS", False)
    browser_args: tuple = ("--no-sandbox", "--disable-dev-shm-usage")

    # --- Timeouts ---
    captcha_timeout_s: float = _env_float("CAPTCHA_TIMEOUT_S", 30)
    nav_timeout_ms: int = _env_int("NAV_TIMEOUT_MS", 30000)
    search_result_timeout_ms: int = _env_int("SEARCH_RESULT_TIMEOUT_MS", 20000)
    download_bootbox_timeout_ms: int = _env_int("DOWNLOAD_BOOTBOX_TIMEOUT_MS", 10000)
    download_timeout_ms: int = _env_int("DOWNLOAD_TIMEOUT_MS", 25000)

    # --- Retries / pacing ---
    max_attempts: int = _env_int("MAX_ATTEMPTS", 3)
    backoff_base_s: float = _env_float("BACKOFF_BASE_S", 2)
    pause_min_s: float = _env_float("PAUSE_MIN_S", 2)
    pause_max_s: float = _env_float("PAUSE_MAX_S", 4)

    # --- Paths ---
    input_csv: Path = _env_path("INPUT_CSV", "data/input.csv")
    output_dir: Path = _env_path("OUTPUT_DIR", "output")

    @property
    def pdf_dir(self) -> Path:
        return self.output_dir / "pdfs"

    @property
    def log_dir(self) -> Path:
        return self.output_dir / "logs"

    @property
    def metrics_file(self) -> Path:
        return self.output_dir / "metrics" / "rpa_metrics.json"

    @property
    def metadata_file(self) -> Path:
        return self.output_dir / "metadata" / "dian_detalle.json"


CONFIG = Config()
