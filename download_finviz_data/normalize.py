"""
Convert old downloads (Finviz's display strings, e.g. '11.08B', '0.95%', '4,633,710', 'Oct 15/a') to the raw-number
format the downloader writes now, with Finviz's field names as headers.

Units match new downloads: marketCap/sharesOutstanding2/sharesFloat in millions, averageVolume in shares, percentages
as plain numbers in percent units. 'Oct 15/a' becomes earningsDate=2026-10-15 (year nearest the file's date) and
earningsTiming=a ('a' after close, 'b' before open, empty if Finviz didn't say). Conversion is exact decimal text, and
any value that doesn't match its column's expected format stops the run rather than being guessed.

Originals are copied to BACKUP_DIR before being rewritten. Files that already have the new header are skipped, so the
tool can be re-run, e.g. after `repair` has rewritten some files in the old format.

    python -m download_finviz_data.normalize --data-dir ~/Downloads/finviz_data [--dry-run]
"""

from __future__ import annotations

import csv
import logging
import re
import shutil
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Optional

from download_finviz_data import data_dir_parser, setup_logging
from download_finviz_data.csvio import file_date, open_text, write_atomic
from download_finviz_data.finviz import write_csv


logger = logging.getLogger(__name__)

BACKUP_DIR = '_backup_formatted'
OLD_FORMAT_FIRST_COLUMN = 'Ticker'
EARNINGS_TIMING = 'earningsTiming'
MISSING = ('', '-')


class NormalizeError(Exception):
    """A file can't be converted without guessing."""


class Kind(Enum):
    TEXT = 'text'
    NUMBER = 'number'          # 9.13 -> 9.13
    PERCENT = 'percent'        # 0.95% -> 0.95
    MILLIONS = 'millions'      # 11.08B -> 11080
    SHARES = 'shares'          # 5.87M -> 5870000
    INTEGER = 'integer'        # 4,633,710 -> 4633710
    EARNINGS = 'earnings'      # Oct 15/a -> 2026-10-15, a


_NUMBER = r'-?\d+(?:\.\d+)?'
_PATTERNS = {
    Kind.NUMBER: re.compile(rf'({_NUMBER})'),
    Kind.PERCENT: re.compile(rf'({_NUMBER})%'),
    Kind.MILLIONS: re.compile(rf'({_NUMBER})([KMB])'),
    Kind.SHARES: re.compile(rf'({_NUMBER})([KMB])'),
    Kind.INTEGER: re.compile(r'(\d{1,3}(?:,\d{3})*)'),
}
_SCALE = {
    Kind.MILLIONS: {'K': Decimal('0.001'), 'M': Decimal(1), 'B': Decimal(1000)},
    Kind.SHARES: {'K': Decimal(10) ** 3, 'M': Decimal(10) ** 6, 'B': Decimal(10) ** 9},
}
_EARNINGS = re.compile(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2})(?:/([ab]))?')
_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')

# Old header (all variants seen 2025-11-27 .. 2026-09-29) -> (new Finviz key, kind).
COLUMNS: dict[str, tuple[str, Kind]] = {
    'Ticker': ('ticker', Kind.TEXT),
    'Company': ('company', Kind.TEXT),
    'Sector': ('sector', Kind.TEXT),
    'Industry': ('industry', Kind.TEXT),
    'Country': ('country', Kind.TEXT),
    'Market Cap': ('marketCap', Kind.MILLIONS),
    'P/E': ('PE', Kind.NUMBER),
    'Fwd P/E': ('forwardPE', Kind.NUMBER),
    'Forward P/E': ('forwardPE', Kind.NUMBER),
    'PEG': ('PEG', Kind.NUMBER),
    'P/S': ('PS', Kind.NUMBER),
    'P/B': ('PB', Kind.NUMBER),
    'P/C': ('PC', Kind.NUMBER),
    'P/FCF': ('PFCF', Kind.NUMBER),
    'Dividend': ('dividendYield', Kind.PERCENT),
    'Payout Ratio': ('payoutRatio', Kind.PERCENT),
    'EPS': ('eps', Kind.NUMBER),
    'EPS This Y': ('epsYoY', Kind.PERCENT),
    'EPS Next Y': ('epsYoY1', Kind.PERCENT),
    'EPS Past 5Y': ('eps5Years', Kind.PERCENT),
    'EPS Next 5Y': ('estLTGrowth', Kind.PERCENT),
    'Sales Past 5Y': ('sales5Years', Kind.PERCENT),
    'EPS Q/Q': ('epsQoQ', Kind.PERCENT),
    'Sales Q/Q': ('salesQoQ', Kind.PERCENT),
    'Outstanding': ('sharesOutstanding2', Kind.MILLIONS),
    'Float': ('sharesFloat', Kind.MILLIONS),
    'Insider Own': ('insiderOwn', Kind.PERCENT),
    'Insider Trans': ('insiderTrans', Kind.PERCENT),
    'Inst Own': ('instOwn', Kind.PERCENT),
    'Inst Trans': ('instTrans', Kind.PERCENT),
    'Short Float': ('shortInterestShare', Kind.PERCENT),
    'Short Ratio': ('shortInterestRatio', Kind.NUMBER),
    'ROA': ('roa', Kind.PERCENT),
    'ROE': ('roe', Kind.PERCENT),
    'ROIC': ('roi', Kind.PERCENT),
    'Curr R': ('curRatio', Kind.NUMBER),
    'Quick R': ('quickRatio', Kind.NUMBER),
    'LTDebt/Eq': ('ltdebtEq', Kind.NUMBER),
    'Debt/Eq': ('debtEq', Kind.NUMBER),
    'Gross M': ('grossMargin', Kind.PERCENT),
    'Oper M': ('operMargin', Kind.PERCENT),
    'Profit M': ('netMargin', Kind.PERCENT),
    'Perf Week': ('perf1w', Kind.PERCENT),
    'Perf Month': ('perf4w', Kind.PERCENT),
    'Perf Quart': ('perf13w', Kind.PERCENT),
    'Perf Half': ('perf26w', Kind.PERCENT),
    'Perf Year': ('perf52w', Kind.PERCENT),
    'Perf YTD': ('perfYtd', Kind.PERCENT),
    'Beta': ('beta', Kind.NUMBER),
    'ATR': ('averageTrueRange', Kind.NUMBER),
    'Volatility W': ('volatility1w', Kind.PERCENT),
    'Volatility M': ('volatility4w', Kind.PERCENT),
    'SMA20': ('sma20', Kind.PERCENT),
    'SMA50': ('sma50', Kind.PERCENT),
    'SMA200': ('sma200', Kind.PERCENT),
    '50D High': ('high50d', Kind.PERCENT),
    '50D Low': ('low50d', Kind.PERCENT),
    '52W High': ('high52w', Kind.PERCENT),
    '52W Low': ('low52w', Kind.PERCENT),
    'RSI': ('rsi', Kind.NUMBER),
    'Change from Open': ('changeopen', Kind.PERCENT),
    'Change from Open %': ('changeopen', Kind.PERCENT),
    'Gap': ('gap', Kind.PERCENT),
    'Recom': ('recom', Kind.NUMBER),
    'Avg Volume': ('averageVolume', Kind.SHARES),
    'Rel Volume': ('relativeVolume', Kind.NUMBER),
    'Price': ('price', Kind.NUMBER),
    'Change': ('change', Kind.PERCENT),
    'Change %': ('change', Kind.PERCENT),
    'Volume': ('volume', Kind.INTEGER),
    'Earnings': ('earningsDate', Kind.EARNINGS),
}


def _decimal_text(value: Decimal) -> str:
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def convert(kind: Kind, value: str) -> str:
    """Convert one display string to raw-number text ('' when missing). Raises ValueError on an unexpected format."""
    if kind is Kind.TEXT:
        return value
    if kind not in _PATTERNS:
        raise ValueError(f'convert() does not handle {kind.value} values')
    value = value.strip()
    if value in MISSING:
        return ''
    match = _PATTERNS[kind].fullmatch(value)
    if not match:
        raise ValueError(f'{value!r} is not a {kind.value} value')
    if kind is Kind.INTEGER:
        return match.group(1).replace(',', '')
    if kind in _SCALE:
        return _decimal_text(Decimal(match.group(1)) * _SCALE[kind][match.group(2)])
    return match.group(1)


def convert_earnings(value: str, file_day: date) -> tuple[str, str]:
    """'Oct 15/a' -> ('YYYY-10-15', 'a'), choosing the year that puts the date nearest `file_day`."""
    value = value.strip()
    if value in MISSING:
        return '', ''
    match = _EARNINGS.fullmatch(value)
    if not match:
        raise ValueError(f'{value!r} is not an earnings value')
    month, day = _MONTHS.index(match.group(1)) + 1, int(match.group(2))
    candidates = []
    for year in range(file_day.year - 4, file_day.year + 5):  # wide enough to reach a leap year for Feb 29
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        raise ValueError(f'{value!r} is not a calendar date')
    nearest = min(candidates, key=lambda d: abs((d - file_day).days))
    return nearest.isoformat(), match.group(3) or ''


def output_columns(header: list[str]) -> list[str]:
    """New column names for an old header, with earningsTiming right after earningsDate."""
    columns = []
    for name in header:
        if name not in COLUMNS:
            raise NormalizeError(f'unknown column {name!r}')
        key, kind = COLUMNS[name]
        columns += [key, EARNINGS_TIMING] if kind is Kind.EARNINGS else [key]
    return columns


def normalize_table(header: list[str], rows: list[list[str]], file_day: date) -> tuple[list[str], list[dict]]:
    """Convert an old file's rows to {new column: raw value} dicts."""
    columns = output_columns(header)
    specs = [(name, *COLUMNS[name]) for name in header]
    converted = []
    for n, row in enumerate(rows, start=1):
        if len(row) != len(header):
            raise NormalizeError(f'row {n} has {len(row)} fields, expected {len(header)}')
        out = {}
        for (name, key, kind), value in zip(specs, row):
            try:
                if kind is Kind.EARNINGS:
                    out[key], out[EARNINGS_TIMING] = convert_earnings(value, file_day)
                else:
                    out[key] = convert(kind, value)
            except ValueError as err:
                raise NormalizeError(f'row {n} column {name!r}: unexpected value {value!r}') from err
        converted.append(out)
    return columns, converted


def normalize_dir(data_dir, dry_run: bool = False) -> dict[str, str]:
    """Normalize every old-format download in `data_dir`. Returns {file name: 'normalized' | 'skipped'}."""
    data_dir = Path(data_dir)
    backup_dir = data_dir / BACKUP_DIR
    summary = {}
    for path in sorted(p for p in data_dir.iterdir() if p.is_file()):
        file_day = file_date(path)
        if file_day is None:
            continue
        with open_text(path, 'r') as f:
            reader = csv.reader(f)
            header = next(reader, [])
            if header[:1] != [OLD_FORMAT_FIRST_COLUMN]:
                summary[path.name] = 'skipped'
                continue
            rows = list(reader)
        try:
            columns, converted = normalize_table(header, rows, file_day)
        except NormalizeError as err:
            raise NormalizeError(f'{path.name}: {err}') from err
        summary[path.name] = 'normalized'
        if dry_run:
            continue
        backup_dir.mkdir(exist_ok=True)
        shutil.copy2(path, backup_dir / path.name)
        write_atomic(path, lambda tmp: write_csv(converted, columns, tmp))
    return summary


def main(argv: Optional[list[str]] = None) -> None:
    args = data_dir_parser('Convert old formatted Finviz downloads to raw numbers').parse_args(argv)
    setup_logging(args.log_level)
    try:
        summary = normalize_dir(args.data_dir, dry_run=args.dry_run)
    except NormalizeError as err:
        logger.error(err)
        sys.exit(1)
    counts = Counter(summary.values())
    logger.info(f'{"would normalize" if args.dry_run else "normalized"} {counts["normalized"]} files, '
                f'skipped {counts["skipped"]} already in the new format')


if __name__ == '__main__':
    main()
