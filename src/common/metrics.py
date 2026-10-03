"""Timing helper and metrics file writer for the RPA run."""
from __future__ import annotations

import json
import statistics
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def stage_timer() -> Iterator[dict]:
    """Context manager that times a stage with perf_counter.

    Usage:
        with stage_timer() as t:
            do_something()
        elapsed = t["seconds"]
    """
    start = time.perf_counter()
    holder = {"seconds": 0.0}
    try:
        yield holder
    finally:
        holder["seconds"] = round(time.perf_counter() - start, 3)


def write_metrics(results: list[dict], path: Path) -> dict:
    # "omitido" (PDF ya existia y era valido) cuenta como exito, pero su
    # t_total es 0.0 (no se cronometro una descarga real) asi que se excluye
    # de las estadisticas de tiempo para no distorsionar el minimo.
    successes = [r for r in results if r.get("estado") in ("ok", "omitido")]
    totals = [r["t_total"] for r in results if r.get("estado") == "ok" and r.get("t_total") is not None]

    summary = {
        "exitos": len(successes),
        "total": len(results),
        "promedio_t_total": round(statistics.mean(totals), 3) if totals else None,
        "mediana_t_total": round(statistics.median(totals), 3) if totals else None,
        "minimo_t_total": round(min(totals), 3) if totals else None,
        "maximo_t_total": round(max(totals), 3) if totals else None,
    }

    payload = {"resultados": results, "resumen": summary}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
