"""Genera las gráficas del README a partir de las métricas reales de samples/metrics.

Uso:
    python scripts/make_charts.py            # lee samples/metrics, escribe docs/img
    python scripts/make_charts.py --metrics output/metrics

Produce cada gráfica en versión clara y oscura (docs/img/*_light.png, *_dark.png)
e imprime en consola un resumen de las cifras para mantener el README sincronizado.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "text": "#0b0b0b",
        "muted": "#52514e",
        "grid": "#e4e3df",
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
    },
    "dark": {
        "surface": "#1a1a19",
        "text": "#ffffff",
        "muted": "#c3c2b7",
        "grid": "#383835",
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500"],
    },
}


def _load(metrics_dir: Path) -> tuple[list, list, dict]:
    rpa = json.loads((metrics_dir / "rpa_metrics.json").read_text(encoding="utf-8"))["resultados"]
    ocr = json.loads((metrics_dir / "ocr_metrics.json").read_text(encoding="utf-8"))["facturas"]
    val = json.loads((metrics_dir / "ocr_validation.json").read_text(encoding="utf-8"))
    return rpa, ocr, val


def _style(ax, t, title: str, subtitle: str) -> None:
    ax.set_facecolor(t["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["grid"])
    ax.tick_params(colors=t["muted"], labelsize=9, length=0)
    ax.xaxis.grid(True, color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", color=t["text"], fontsize=13, fontweight="bold", pad=26)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=t["muted"], fontsize=9.5, va="bottom")


def chart_rpa(rpa: list, out: Path, theme: str) -> None:
    t = THEMES[theme]
    rows = sorted(rpa, key=lambda r: r["n"], reverse=True)
    labels = [f"Factura {r['n']}" for r in rows]
    parts = {
        "Navegación, metadatos y captcha de descarga": [
            r["t_total"] - r["t_captcha"] - r["t_busqueda"] - r["t_descarga"] for r in rows
        ],
        "Captcha de búsqueda": [r["t_captcha"] for r in rows],
        "Búsqueda": [r["t_busqueda"] for r in rows],
        "Descarga del PDF": [r["t_descarga"] for r in rows],
    }
    fig, ax = plt.subplots(figsize=(9, 5.2), facecolor=t["surface"])
    left = [0.0] * len(rows)
    for color, (name, vals) in zip(t["series"], parts.items()):
        ax.barh(labels, vals, left=left, color=color, height=0.62, edgecolor=t["surface"], linewidth=2, label=name)
        left = [a + b for a, b in zip(left, vals)]
    for y, total in enumerate(left):
        ax.text(total + 0.15, y, f"{total:.1f} s", va="center", color=t["text"], fontsize=9)
    ax.set_xlim(0, max(left) * 1.12)
    ax.set_xlabel("segundos", color=t["muted"], fontsize=9)
    mean = st.mean(r["t_total"] for r in rpa)
    _style(ax, t, "Numeral 1 · Tiempo del RPA por factura",
           f"10/10 descargas en el primer intento · promedio {mean:.2f} s por factura")
    leg = ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.24), ncol=4, frameon=False, fontsize=8.5)
    for txt in leg.get_texts():
        txt.set_color(t["text"])
    fig.tight_layout()
    fig.savefig(out, dpi=160, facecolor=t["surface"])
    plt.close(fig)


OCR_STAGES = {
    "render": "PDF → PNG (300 DPI)",
    "ocr_encabezado": "OCR del encabezado",
    "deteccion_tabla": "Detección de la tabla (600 DPI)",
    "ocr_tabla": "OCR de la tabla por celdas",
    "parseo": "Normalización y CSV",
}


def chart_ocr_stages(ocr: list, out: Path, theme: str) -> None:
    t = THEMES[theme]
    names = list(OCR_STAGES.values())[::-1]
    vals = [st.mean(f["tiempos"].get(k, 0.0) for f in ocr) for k in OCR_STAGES][::-1]
    fig, ax = plt.subplots(figsize=(9, 3.8), facecolor=t["surface"])
    ax.barh(names, vals, color=t["series"][0], height=0.55, edgecolor=t["surface"], linewidth=2)
    for y, v in enumerate(vals):
        ax.text(v + 0.04, y, f"{v:.2f} s", va="center", color=t["text"], fontsize=9)
    ax.set_xlim(0, max(vals) * 1.2)
    ax.set_xlabel("segundos (promedio por factura)", color=t["muted"], fontsize=9)
    total = st.mean(f["tiempos"]["total"] for f in ocr)
    _style(ax, t, "Numeral 2 · Tiempo promedio del OCR por etapa",
           f"Total promedio {total:.2f} s por factura · 10 facturas, 20 páginas")
    fig.tight_layout()
    fig.savefig(out, dpi=160, facecolor=t["surface"])
    plt.close(fig)


def accuracy(val: dict) -> dict[str, float]:
    inv = list(val.values())
    items = sum(v["n_items_referencia"] for v in inv)

    def weighted(key: str) -> float:
        return sum(v[key] * v["n_items_referencia"] for v in inv) / items

    return {
        "Número de factura": 100 * sum(v["numero_factura_ok"] for v in inv) / len(inv),
        "Fecha de emisión": 100 * sum(v["fecha_emision_ok"] for v in inv) / len(inv),
        "NIT del emisor": 100 * sum(v["nit_emisor_ok"] for v in inv) / len(inv),
        "Código": weighted("codigo_pct"),
        "Descripción (exacta)": weighted("descripcion_pct"),
        "Cantidad": weighted("cantidad_pct"),
        "Precio unitario": weighted("precio_unitario_pct"),
    }


def chart_accuracy(val: dict, out: Path, theme: str) -> None:
    t = THEMES[theme]
    acc = accuracy(val)
    names, vals = list(acc)[::-1], list(acc.values())[::-1]
    inv = list(val.values())
    items = sum(v["n_items_referencia"] for v in inv)
    cer = 100 * sum(v["descripcion_cer_avg"] * v["n_items_referencia"] for v in inv) / items
    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=t["surface"])
    ax.barh(names, vals, color=t["series"][0], height=0.55, edgecolor=t["surface"], linewidth=2)
    for y, v in enumerate(vals):
        ax.text(v + 1, y, f"{v:.1f} %", va="center", color=t["text"], fontsize=9)
    ax.set_xlim(0, 112)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("% de valores idénticos a la referencia", color=t["muted"], fontsize=9)
    _style(ax, t, "Numeral 2 · Precisión del OCR por campo",
           f"Encabezado: 10 facturas · Productos: {items} ítems · Error de caracteres en descripción: {cer:.2f} %")
    fig.tight_layout()
    fig.savefig(out, dpi=160, facecolor=t["surface"])
    plt.close(fig)


def summary(rpa: list, ocr: list, val: dict) -> None:
    def row(vals: list[float]) -> str:
        return f"{st.mean(vals):.2f} | {st.median(vals):.2f} | {min(vals):.2f} | {max(vals):.2f} | {sum(vals):.2f}"

    print("RPA (promedio | mediana | mín | máx | total)")
    print("  navegacion/metadatos/captcha_descarga", row([r["t_total"] - r["t_captcha"] - r["t_busqueda"] - r["t_descarga"] for r in rpa]))
    for k in ("t_captcha", "t_busqueda", "t_descarga", "t_total"):
        print(f"  {k}", row([r[k] for r in rpa]))
    print("OCR (promedio | mediana | mín | máx | total)")
    for k in list(OCR_STAGES) + ["total"]:
        print(f"  {k}", row([f["tiempos"].get(k, 0.0) for f in ocr]))
    print("Precisión:", {k: round(v, 1) for k, v in accuracy(val).items()})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default=str(ROOT / "samples" / "metrics"))
    parser.add_argument("--out", default=str(ROOT / "docs" / "img"))
    args = parser.parse_args()
    rpa, ocr, val = _load(Path(args.metrics))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        chart_rpa(rpa, out / f"rpa_tiempos_{theme}.png", theme)
        chart_ocr_stages(ocr, out / f"ocr_etapas_{theme}.png", theme)
        chart_accuracy(val, out / f"ocr_precision_{theme}.png", theme)
    summary(rpa, ocr, val)


if __name__ == "__main__":
    main()
