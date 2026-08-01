"""Summarise the LATENCY lines written by `infrastructure.services.latency_log`.

    python tools/latency_report.py
    python tools/latency_report.py --since 2026-08-01
    python tools/latency_report.py --logs logs/error_log.txt

Groups every record by pipeline and stage and reports n / p50 / p90 / max, plus
the mean of numeric extra fields (rtf, audio_ms, ...). Standard library only, so
it runs without activating the virtualenv.
"""

from __future__ import annotations

import argparse
import glob
import re
from collections import defaultdict
from pathlib import Path

LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
    r".*LATENCY pipeline=(?P<pipeline>\S+) stage=(?P<stage>\S+) ms=(?P<ms>[\d.]+)"
    r"(?P<extra>.*)$"
)
FIELD = re.compile(r"(\w+)=([^\s]+)")


def parse(paths: list[str], since: str | None) -> dict:
    records: dict[tuple[str, str], list] = defaultdict(list)
    for path in paths:
        try:
            lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            match = LINE.match(line)
            if not match:
                continue
            if since and match["ts"][: len(since)] < since:
                continue
            extra = dict(FIELD.findall(match["extra"] or ""))
            records[(match["pipeline"], match["stage"])].append(
                (float(match["ms"]), extra)
            )
    return records


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. Values must be sorted."""
    if not values:
        return float("nan")
    index = min(len(values) - 1, int(len(values) * fraction))
    return values[index]


def mean_numeric_fields(entries: list) -> dict[str, float]:
    sums: dict[str, list[float]] = defaultdict(list)
    for _, extra in entries:
        for key, raw in extra.items():
            try:
                sums[key].append(float(raw))
            except ValueError:
                continue
    return {k: sum(v) / len(v) for k, v in sums.items() if v}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--logs",
        default="logs/error_log.txt*",
        help="Glob de archivos de log (por defecto logs/error_log.txt*)",
    )
    parser.add_argument("--since", default=None, help="Solo registros desde esta fecha, p. ej. 2026-08-01")
    args = parser.parse_args()

    paths = sorted(glob.glob(args.logs))
    if not paths:
        print(f"No se encontraron logs en: {args.logs}")
        return 1

    records = parse(paths, args.since)
    if not records:
        print(
            "No hay registros LATENCY. Corré la app o el banco primero:\n"
            "  python tools/bench_transcription.py <audio.wav>"
        )
        return 1

    print(f"\n{'Pipeline':<20}{'Etapa':<24}{'n':>6}{'p50':>10}{'p90':>10}{'max':>10}")
    print("-" * 80)
    for (pipeline, stage), entries in sorted(records.items()):
        values = sorted(ms for ms, _ in entries)
        print(
            f"{pipeline:<20}{stage:<24}{len(values):>6}"
            f"{percentile(values, 0.5):>9.0f}ms"
            f"{percentile(values, 0.9):>9.0f}ms"
            f"{values[-1]:>9.0f}ms"
        )
        extras = mean_numeric_fields(entries)
        if extras:
            detail = "  ".join(f"{k}~{v:.2f}" for k, v in sorted(extras.items()))
            print(f"{'':<44}{detail}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
