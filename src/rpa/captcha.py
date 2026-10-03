"""Cloudflare Turnstile solving.

The DIAN site renders Turnstile as a "managed" widget: it may auto-resolve
invisibly, or it may show a checkbox inside an iframe hosted at
challenges.cloudflare.com. We poll the hidden response input and, the moment
a checkbox appears in any matching iframe, click it immediately (no fixed
sleep) -- see docs/spike_turnstile.md for the validation behind this.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from patchright.sync_api import Page

from .config import CONFIG


@dataclass
class CaptchaResult:
    resolved: bool
    seconds: float
    clicked_checkbox: bool


class CaptchaSolver:
    def solve(self, page: Page) -> CaptchaResult:
        raise NotImplementedError


class TurnstileBrowserSolver(CaptchaSolver):
    """Solves Turnstile widgets already present on the current page.

    A single page can have more than one widget (e.g. the DIAN search form
    has one, and the document-detail page has a second, separate one scoped
    to the PDF download). Call solve() once per widget, right after it
    becomes relevant and before anything (like a modal) can cover it.
    """

    def __init__(self, timeout_s: float | None = None, poll_interval_s: float = 0.3):
        self.timeout_s = CONFIG.captcha_timeout_s if timeout_s is None else timeout_s
        self.poll_interval_s = poll_interval_s

    def solve(self, page: Page) -> CaptchaResult:
        start = time.perf_counter()
        deadline = start + self.timeout_s
        clicked = False

        while time.perf_counter() < deadline:
            values = page.eval_on_selector_all(
                CONFIG.turnstile_response_selector, "els => els.map(e => e.value)"
            )
            if any(values):
                return CaptchaResult(True, round(time.perf_counter() - start, 3), clicked)

            for frame in page.frames:
                if "challenges.cloudflare.com" not in (frame.url or ""):
                    continue
                try:
                    checkbox = frame.get_by_role("checkbox")
                    if checkbox.count() > 0:
                        checkbox.click(timeout=1000)
                        clicked = True
                except Exception:
                    pass

            time.sleep(self.poll_interval_s)

        return CaptchaResult(False, round(time.perf_counter() - start, 3), clicked)
