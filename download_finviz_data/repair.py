"""
Repair downloads whose ticker column was truncated to its first character (files from 2026-07-15 to 2026-09-29,
written by the old HTML scraper after a Finviz markup change).

Every other column survived, so each ticker is recovered from the company name using the intact files in the same
directory as references, constrained by:
  * the surviving first letter,
  * the reference sighting nearest in time (tickers change),
  * Finviz's row order: files are sorted by ticker, so recovered tickers must strictly increase.
Anything that can't be recovered with certainty keeps its single letter and is listed in the report.

Originals are copied to BACKUP_DIR before being rewritten; later runs read from the backups, so the repair can be
re-run when newer reference files exist (e.g. a fresh full download to recover post-July listings).

    python -m download_finviz_data.repair --data-dir ~/Downloads/finviz_data [--dry-run]
"""

from __future__ import annotations

import bisect
import csv
import logging
import shutil
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

from download_finviz_data import data_dir_parser, setup_logging
from download_finviz_data.csvio import file_date, open_text, write_atomic


logger = logging.getLogger(__name__)

BACKUP_DIR = '_backup_truncated'
REPORT_FILE = '_repair_report.csv'
REPORT_HEADER = ['file', 'row', 'letter', 'company', 'reason']


class Row(NamedTuple):
    letter: str
    company: str


def is_truncated(tickers: Iterable[str]) -> bool:
    """True if every ticker is a single character (real one-letter tickers exist, but never all of them)."""
    tickers = list(tickers)
    return bool(tickers) and all(len(t) <= 1 for t in tickers)


def read_table(path) -> tuple[list[str], list[list[str]], int, int]:
    """Header, data rows, and the ticker/company column indexes (old 'Ticker' or new 'ticker' headers)."""
    with open_text(path, 'r') as f:
        header, *rows = csv.reader(f)
    names = [h.strip().lower() for h in header]
    if 'ticker' not in names or 'company' not in names:
        raise ValueError(f'{path}: header has no ticker/company columns')
    return header, rows, names.index('ticker'), names.index('company')


def _write_old_format(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Rewrite atomically in the old scraper's exact dialect (all fields quoted, CRLF)."""
    def write(tmp: Path) -> None:
        with open_text(tmp, 'w') as f:
            csv.writer(f, quotechar='"', quoting=csv.QUOTE_ALL).writerows([header, *rows])

    write_atomic(path, write)


def _days_outside(span: tuple[date, date], when: date) -> int:
    first, last = span
    return 0 if first <= when <= last else min(abs((first - when).days), abs((when - last).days))


class ReferenceIndex:
    """For each company name, the date span over which each ticker was seen in intact files."""

    def __init__(self):
        self.spans: dict[str, dict[str, tuple[date, date]]] = defaultdict(dict)

    def add(self, when: date, pairs: Iterable[tuple[str, str]]) -> None:
        for company, ticker in pairs:
            first, last = self.spans[company].get(ticker, (when, when))
            self.spans[company][ticker] = (min(first, when), max(last, when))

    def candidates(self, company: str, letter: str, when: date) -> list[str]:
        """Tickers of `company` starting with `letter`, keeping only those seen nearest to `when`; sorted."""
        distance = {ticker: _days_outside(span, when)
                    for ticker, span in self.spans.get(company, {}).items() if ticker.startswith(letter)}
        if not distance:
            return []
        nearest = min(distance.values())
        return sorted(ticker for ticker, d in distance.items() if d == nearest)


def longest_increasing(values: list[str]) -> set[int]:
    """Indexes of one longest strictly increasing subsequence (patience sorting, O(n log n))."""
    tails, tail_index, previous = [], [], [-1] * len(values)
    for i, value in enumerate(values):
        k = bisect.bisect_left(tails, value)
        if k == len(tails):
            tails.append(value)
            tail_index.append(i)
        else:
            tails[k] = value
            tail_index[k] = i
        previous[i] = tail_index[k - 1] if k else -1
    keep, i = set(), (tail_index[-1] if tail_index else -1)
    while i != -1:
        keep.add(i)
        i = previous[i]
    return keep


def resolve_tickers(rows: list[Row], index: ReferenceIndex, when: date) -> list[tuple[str, Optional[str]]]:
    """
    (ticker, None) for each recovered row, or (letter, reason) where reason is 'not_found', 'ambiguous' or
    'order_conflict'. Rows sharing a company name and letter (share classes) are matched to the candidates in order.
    """
    result = [(row.letter, 'not_found') for row in rows]
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        groups[row].append(i)
    for row, positions in groups.items():
        candidates = index.candidates(row.company, row.letter, when)
        if not candidates:
            continue
        if len(candidates) == len(positions):
            for i, ticker in zip(positions, candidates):
                result[i] = (ticker, None)
        else:
            for i in positions:
                result[i] = (row.letter, 'ambiguous')

    resolved = [i for i, (_, reason) in enumerate(result) if reason is None]
    keep = longest_increasing([result[i][0] for i in resolved])
    for position, i in enumerate(resolved):
        if position not in keep:
            result[i] = (rows[i].letter, 'order_conflict')
    return result


def repair_dir(data_dir, dry_run: bool = False) -> dict[str, dict[str, int]]:
    """Repair every truncated download in `data_dir`. Returns per-file counts of resolved rows and reasons."""
    data_dir = Path(data_dir)
    backup_dir = data_dir / BACKUP_DIR
    backed_up = {p.name for p in backup_dir.iterdir()} if backup_dir.is_dir() else set()

    def source(path: Path) -> Path:
        return backup_dir / path.name if path.name in backed_up else path

    # Pass 1: index every intact file; remember the truncated ones. A repaired file is read from its (truncated)
    # backup, so it is always a target and never a reference: a leftover single letter can't "confirm" itself.
    index, targets = ReferenceIndex(), []
    for path in sorted(p for p in data_dir.iterdir() if p.is_file()):
        when = file_date(path)
        if when is None:
            continue
        _, rows, ticker_col, company_col = read_table(source(path))
        if is_truncated(r[ticker_col] for r in rows):
            targets.append(path)
        else:
            index.add(when, ((r[company_col], r[ticker_col]) for r in rows))
    logger.info(f'{len(targets)} truncated files; {len(index.spans)} company names in references')

    # Pass 2: one target at a time (each is ~6 MB).
    summary, report = {}, []
    for path in targets:
        header, rows, ticker_col, company_col = read_table(source(path))
        resolved = resolve_tickers([Row(r[ticker_col], r[company_col]) for r in rows], index, file_date(path))
        counts = Counter(reason for _, reason in resolved)
        summary[path.name] = {'rows': len(rows), 'resolved': counts.pop(None, 0), **counts}
        logger.info(f'{path.name}: {summary[path.name]}')
        for n, (row, (_, reason)) in enumerate(zip(rows, resolved), start=1):
            if reason:
                report.append([path.name, n, row[ticker_col], row[company_col], reason])
        if dry_run:
            continue
        backup_dir.mkdir(exist_ok=True)
        if path.name not in backed_up:
            shutil.copy2(path, backup_dir / path.name)
        for row, (ticker, _) in zip(rows, resolved):
            row[ticker_col] = ticker
        _write_old_format(path, header, rows)

    if targets and not dry_run:
        with open_text(data_dir / REPORT_FILE, 'w') as f:
            csv.writer(f).writerows([REPORT_HEADER, *report])
    return summary


def main(argv: Optional[list[str]] = None) -> None:
    args = data_dir_parser('Recover truncated tickers in old Finviz downloads').parse_args(argv)
    setup_logging(args.log_level)

    summary = repair_dir(args.data_dir, dry_run=args.dry_run)
    totals = Counter()
    for counts in summary.values():
        totals.update(counts)
    logger.info(f'{"would repair" if args.dry_run else "repaired"} {len(summary)} files: {dict(totals)}')


if __name__ == '__main__':
    main()
