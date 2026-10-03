"""Thin wrapper around pytesseract: plain text and word-level data (bbox +
confidence) from an image or numpy array.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd
import pytesseract
from PIL import Image

from .config import OCR_CONFIG

ImageLike = Union[str, Path, Image.Image, "np.ndarray"]


@dataclass
class Word:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int
    line_num: int
    block_num: int
    par_num: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2


def _config_string(
    psm: int, whitelist: Optional[str] = None, extra: Optional[str] = None, oem: Optional[int] = None
) -> str:
    cfg = f"--psm {psm}"
    if oem is not None:
        cfg += f" --oem {oem}"
    if OCR_CONFIG.tessdata_dir:
        cfg += f' --tessdata-dir "{OCR_CONFIG.tessdata_dir}"'
    if whitelist:
        cfg += f' -c tessedit_char_whitelist="{whitelist}"'
    if extra:
        cfg += f" {extra}"
    return cfg


def _coerce_image(image: ImageLike) -> ImageLike:
    # pytesseract only accepts str, PIL.Image or numpy.ndarray -- not Path.
    return str(image) if isinstance(image, Path) else image


def image_to_text(
    image: ImageLike,
    psm: int,
    lang: str = OCR_CONFIG.lang,
    oem: Optional[int] = None,
    extra_config: Optional[str] = None,
) -> str:
    return pytesseract.image_to_string(
        _coerce_image(image), lang=lang, config=_config_string(psm, extra=extra_config, oem=oem)
    )


def image_to_words(
    image: ImageLike,
    psm: int,
    lang: str = OCR_CONFIG.lang,
    whitelist: Optional[str] = None,
    extra_config: Optional[str] = None,
    oem: Optional[int] = None,
) -> list[Word]:
    """Returns one Word per recognized token, ignoring tokens with empty text
    or confidence < 0 (Tesseract's TSV marks non-text rows -- block/line/page
    placeholders -- with conf=-1; those carry no evidence and must not be
    treated as low-confidence real words)."""
    image = _coerce_image(image)
    data = pytesseract.image_to_data(
        image,
        lang=lang,
        config=_config_string(psm, whitelist, extra_config, oem),
        output_type=pytesseract.Output.DATAFRAME,
    )
    data = data.dropna(subset=["text"])
    data = data[data["text"].astype(str).str.strip() != ""]
    data = data[data["conf"].astype(float) >= 0]

    words: list[Word] = []
    for _, row in data.iterrows():
        words.append(
            Word(
                text=str(row["text"]),
                conf=float(row["conf"]),
                left=int(row["left"]),
                top=int(row["top"]),
                width=int(row["width"]),
                height=int(row["height"]),
                line_num=int(row["line_num"]),
                block_num=int(row["block_num"]),
                par_num=int(row["par_num"]),
            )
        )
    return words


def words_to_text(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def min_confidence(words: list[Word]) -> Optional[float]:
    confs = [w.conf for w in words]
    return min(confs) if confs else None
