"""PDF capture strategies for the "Descargar PDF" link.

Verified behaviour (see docs/spike_turnstile.md): clicking a.downloadLink
always opens a bootbox modal ("este archivo contiene contrasena..."), and
only accepting it (with the page's download-scoped Turnstile already solved)
triggers a fetch() to /Document/DownloadPDF whose response is turned into a
blob and "downloaded" via a synthetic <a download> click.

Strategy order (first one that yields valid %PDF bytes wins), as requested:
  a) page.expect_download()                      -- catches the blob download
  b) network response interception (content-type: application/pdf)
  c) blob: URL -> fetch -> arrayBuffer -> base64, done from page JS
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Optional

from patchright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from .config import CONFIG


class DownloadError(Exception):
    pass


@dataclass
class DownloadResult:
    data: bytes
    method: str
    endpoint: str


def _is_target_pdf_response(response) -> bool:
    content_type = response.headers.get("content-type", "")
    return (
        "application/pdf" in content_type.lower()
        and CONFIG.download_endpoint_substr in response.url
    )


def download_pdf(page: Page, cufe: str) -> DownloadResult:
    captured: dict = {}

    def on_response(response):
        if "response" not in captured and _is_target_pdf_response(response):
            captured["response"] = response

    page.on("response", on_response)
    try:
        page.click(CONFIG.download_link_selector)
        page.wait_for_selector(
            CONFIG.bootbox_visible_selector, timeout=CONFIG.download_bootbox_timeout_ms
        )

        data: Optional[bytes] = None
        method: Optional[str] = None
        download_obj = None

        # Strategy a: expect_download
        try:
            with page.expect_download(timeout=CONFIG.download_timeout_ms) as dl_info:
                page.click(CONFIG.bootbox_accept_selector)
            download_obj = dl_info.value
            tmp_path = CONFIG.pdf_dir / f"_tmp_{cufe[:12]}.pdf"
            CONFIG.pdf_dir.mkdir(parents=True, exist_ok=True)
            download_obj.save_as(str(tmp_path))
            data = tmp_path.read_bytes()
            tmp_path.unlink(missing_ok=True)
            method = "expect_download"
        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass  # the download event may have fired even if save_as() failed

        # Strategy b: network response interception
        if data is None and "response" in captured:
            resp = captured["response"]
            data = resp.body()
            method = "network_response"

        # Strategy c: blob: URL -> fetch -> base64 from page JS
        if data is None and download_obj is not None and download_obj.url.startswith("blob:"):
            b64 = page.evaluate(
                """async (blobUrl) => {
                    const resp = await fetch(blobUrl);
                    const buf = await resp.arrayBuffer();
                    const bytes = new Uint8Array(buf);
                    let binary = '';
                    for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
                    return btoa(binary);
                }""",
                download_obj.url,
            )
            if b64:
                data = base64.b64decode(b64)
                method = "blob_base64"

        if not data or not data.startswith(b"%PDF"):
            raise DownloadError(
                "no se pudo capturar un PDF valido (no inicia con %PDF o esta vacio)"
            )

        # Always report the real server endpoint when we saw it, even if the
        # bytes themselves came from a blob: download.
        if "response" in captured:
            endpoint = captured["response"].url.split("?")[0]
        elif download_obj is not None:
            endpoint = download_obj.url.split("?")[0]
        else:
            endpoint = CONFIG.download_endpoint_substr

        return DownloadResult(data=data, method=method, endpoint=endpoint)
    finally:
        page.remove_listener("response", on_response)
