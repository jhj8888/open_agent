#!/usr/bin/env python3
"""Summarize JaCoCo class reports using their named CSV counters."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable, TextIO


METRICS = (('Lines', 'LINE'), ('Branches', 'BRANCH'), ('Methods', 'METHOD'))
COUNTERS = tuple(f'{kind}_{suffix}' for _, kind in METRICS for suffix in ('MISSED', 'COVERED'))


def read_counters(report: TextIO) -> dict[str, int]:
    reader = csv.DictReader(report)
    missing = set(COUNTERS).difference(reader.fieldnames or [])
    if missing:
        raise ValueError(f"Missing JaCoCo columns: {', '.join(sorted(missing))}")
    totals = dict.fromkeys(COUNTERS, 0)
    for row in reader:
        for name in COUNTERS:
            value = int(row[name])
            if value < 0:
                raise ValueError(f'Negative JaCoCo counter: {name}')
            totals[name] += value
    return totals


def summarize(reports: Iterable[Path]) -> dict[str, int]:
    totals = dict.fromkeys(COUNTERS, 0)
    for path in reports:
        with path.open(encoding='utf-8', newline='') as report:
            for name, value in read_counters(report).items():
                totals[name] += value
    return totals


def format_summary(totals: dict[str, int]) -> str:
    lines = []
    for label, kind in METRICS:
        covered = totals[f'{kind}_COVERED']
        total = covered + totals[f'{kind}_MISSED']
        result = f'{covered / total:.1%} ({covered}/{total})' if total else 'not measured (0/0)'
        lines.append(f'{label + ":":<10}{result}')
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Maven project root containing module target directories')
    args = parser.parse_args()
    reports = sorted(args.root.glob('**/target/site/jacoco/jacoco.csv'))
    if not reports:
        parser.error('No JaCoCo CSV reports found')
    print(format_summary(summarize(reports)))


if __name__ == '__main__':
    main()
