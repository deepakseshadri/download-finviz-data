"""
Download every row and every field of the Finviz stock and ETF screeners.

Finviz embeds each screener page's data as JSON in a <script type="application/json"> tag; that payload is the
source of truth (the visible HTML grid is virtualized and only renders a subset of the columns).
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import random
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests
from bs4 import BeautifulSoup as BSoup


logger = logging.getLogger(__name__)

BASE_URL = 'https://finviz.com/screener'
SCREENERS = ('stocks', 'etf')
VIEW = 151
# Ask for every column id; Finviz ignores ids it doesn't have, so new columns are picked up automatically.
COLUMN_IDS = ','.join(str(i) for i in range(0, 301))
USER_AGENT = 'Mozilla 5.10'

# Cloudflare blocks the IP for 10+ minutes after ~50 pages at ~3.6s spacing, so go slow and, when blocked, wait long.
DEFAULT_DELAY = 10.0
JITTER_RANGE = (1.10, 1.30)  # only ever wait longer than asked, never sooner
BACKOFF_DELAYS = (15 * 60, 30 * 60, 30 * 60, 30 * 60)
# Cap on the sum of all retry waits in one run, so a run never overlaps the next day's.
MAX_TOTAL_BACKOFF = 4 * 60 * 60
PROGRESS_EVERY = 50  # pages
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
REQUEST_TIMEOUT = 30

# Row groups whose keys become columns without a prefix; other nested groups (e.g. range52w) get 'group.key'.
UNPREFIXED_GROUPS = ('text', 'numbers')
LEADING_COLUMNS = ('ticker', 'company')
FLOAT_DECIMALS = 4
# Fail if fewer unique tickers than this fraction of the screener's totalCount; small drift during a long run is
# expected (listings change), large shortfalls mean the data is broken.
MIN_ROW_FRACTION = 0.99


class FinvizError(Exception):
    """Unrecoverable problem downloading or validating Finviz data."""


def jittered(base: float, uniform: Callable[[float, float], float] = random.uniform) -> float:
    """Stretch a delay by a random +10..30% so requests don't arrive on a fixed beat."""
    return base * uniform(*JITTER_RANGE)


def build_url(screener: str, first_row: int) -> str:
    """URL of the screener page whose first row is `first_row` (Finviz's 1-based `r` parameter)."""
    if screener not in SCREENERS:
        raise ValueError(f'unknown screener {screener!r}; expected one of {SCREENERS}')
    return f'{BASE_URL}/{screener}?v={VIEW}&c={COLUMN_IDS}&r={first_row}'


def is_challenge(html: str) -> bool:
    """True if the page is a Cloudflare bot challenge instead of content."""
    return '<title>Just a moment...</title>' in html and 'challenge-platform' in html


def extract_payload(html: str) -> dict:
    """Return the screener's `data` object: {'rows', 'totalCount', 'firstRow', 'pageSize'}."""
    for script in BSoup(html, 'html.parser').find_all('script', type='application/json'):
        try:
            payload = json.loads(script.string or '')
        except json.JSONDecodeError as err:
            raise FinvizError(f'screener payload is not valid JSON: {err}') from err
        data = payload.get('data') if isinstance(payload, dict) else None
        if isinstance(data, dict) and 'rows' in data:
            return data
    raise FinvizError('screener payload not found in page; Finviz may have changed its layout')


def _normalize(value):
    return round(value, FLOAT_DECIMALS) if isinstance(value, float) else value


def flatten_row(row: dict) -> dict:
    """Flatten one payload row into {column: value}, rounding floats."""
    flat = {}
    for group, value in row.items():
        if isinstance(value, dict):
            prefix = '' if group in UNPREFIXED_GROUPS else f'{group}.'
            items = ((f'{prefix}{key}', item) for key, item in value.items())
        else:
            items = ((group, value),)
        for column, item in items:
            if column in flat:
                ticker = row.get('text', {}).get('ticker')
                raise FinvizError(f'column {column!r} appears in more than one row group (ticker {ticker})')
            flat[column] = _normalize(item)
    return flat


def page_offsets(total: int, page_size: int) -> list[int]:
    """1-based `r` values of every page needed to cover `total` rows."""
    if page_size <= 0:
        raise FinvizError(f'invalid page size {page_size}')
    return list(range(1, total + 1, page_size))


def collect_columns(rows: Iterable[dict]) -> list[str]:
    """Union of all row keys: LEADING_COLUMNS first, the rest in first-seen order."""
    seen = dict.fromkeys(LEADING_COLUMNS)
    for row in rows:
        seen.update(dict.fromkeys(row))
    return list(seen)


def validate_rows(rows: list[dict], expected_total: Optional[int], screener: str) -> list[dict]:
    """
    Reject rows without tickers, drop duplicate tickers (keeping the first), and check the row count against the
    screener's totalCount. Returns the de-duplicated rows.
    """
    unique = {}
    for row in rows:
        ticker = row.get('ticker')
        if not ticker:
            raise FinvizError(f'{screener}: row without a ticker: {row}')
        unique.setdefault(ticker, row)
    if len(unique) != len(rows):
        logger.warning(f'{screener}: dropped {len(rows) - len(unique)} duplicate ticker rows')

    if expected_total is not None:
        if len(unique) < expected_total * MIN_ROW_FRACTION:
            raise FinvizError(f'{screener}: only {len(unique)} unique tickers, expected {expected_total}')
        if len(unique) != expected_total:
            logger.warning(f'{screener}: got {len(unique)} unique tickers, Finviz reported {expected_total}')
    return list(unique.values())


def write_csv(rows: list[dict], columns: list[str], path) -> None:
    """Write rows as CSV; gzip-compressed when the path ends in .gz."""
    path = Path(path)
    opener = gzip.open if path.suffix.lower() == '.gz' else open
    logger.info(f'writing {len(rows)} rows x {len(columns)} columns to {path}')
    try:
        with opener(path, 'wt', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns, restval='')
            writer.writeheader()
            writer.writerows(rows)
    except OSError as err:
        raise FinvizError(f'could not write {path}: {err}') from err


class FinvizClient:
    """Fetches screener pages over one session, politely spaced and retried."""

    def __init__(self, session: Optional[requests.Session] = None, delay: float = DEFAULT_DELAY,
                 sleep: Callable[[float], None] = time.sleep,
                 uniform: Callable[[float, float], float] = random.uniform):
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})
        self.delay = delay
        self._sleep = sleep
        self._uniform = uniform
        self._has_requested = False
        self._total_backoff = 0.0

    def _wait(self, base: float) -> None:
        seconds = jittered(base, self._uniform)
        logger.debug(f'sleeping {seconds:.1f}s')
        self._sleep(seconds)

    def _backoff(self, base: float) -> None:
        """Wait before a retry, unless that would exceed the run's MAX_TOTAL_BACKOFF."""
        seconds = jittered(base, self._uniform)
        if self._total_backoff + seconds > MAX_TOTAL_BACKOFF:
            raise FinvizError(f'retry waits would exceed {MAX_TOTAL_BACKOFF / 3600:.1f} hours this run; giving up')
        logger.info(f'retrying in {seconds / 60:.1f} min')
        self._sleep(seconds)
        self._total_backoff += seconds

    def _get(self, url: str) -> Optional[str]:
        """One attempt. Returns page HTML, or None if the failure is worth retrying."""
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as err:
            logger.warning(f'request failed for {url}: {err}')
            return None
        if is_challenge(response.text):
            logger.warning(f'Cloudflare challenge (HTTP {response.status_code}) for {url}')
            return None
        if response.status_code in RETRYABLE_STATUS:
            logger.warning(f'HTTP {response.status_code} for {url}')
            return None
        if response.status_code != 200:
            raise FinvizError(f'HTTP {response.status_code} for {url}')
        return response.text

    def fetch(self, screener: str, first_row: int) -> dict:
        """Fetch one page and return its payload data, retrying challenges and transient errors with backoff."""
        url = build_url(screener, first_row)
        if self._has_requested:
            self._wait(self.delay)
        self._has_requested = True

        for attempt, backoff in enumerate((0.0, *BACKOFF_DELAYS)):
            if attempt:
                self._backoff(backoff)
            logger.debug(f'fetching {url}')
            html = self._get(url)
            if html is not None:
                return extract_payload(html)
        raise FinvizError(f'gave up on {screener} r={first_row} after {len(BACKOFF_DELAYS) + 1} attempts '
                          f'(Cloudflare challenge or transient error)')

    def download_screener(self, screener: str, first_page_only: bool = False) -> list[dict]:
        """All rows of one screener, flattened and validated."""
        first = self.fetch(screener, 1)
        total, page_size = first['totalCount'], first['pageSize']
        offsets = page_offsets(total, page_size)
        logger.info(f'{screener}: {total} rows over {len(offsets)} pages')

        remaining = [] if first_page_only else offsets[1:]
        raw = list(first['rows'])
        for page, r in enumerate(remaining, start=2):
            data = self.fetch(screener, r)
            if not data['rows']:
                raise FinvizError(f'{screener}: page r={r} returned no rows')
            if data['firstRow'] != r - 1:
                raise FinvizError(f'{screener}: page r={r} returned firstRow={data["firstRow"]}, expected {r - 1}')
            if data['totalCount'] != total:
                logger.warning(f'{screener}: totalCount changed from {total} to {data["totalCount"]} mid-run')
                total = data['totalCount']
            raw += data['rows']
            if page % PROGRESS_EVERY == 0:
                logger.info(f'{screener}: {page}/{len(offsets)} pages done')

        rows = [flatten_row(row) for row in raw]
        return validate_rows(rows, None if first_page_only else total, screener)

    def download_all(self, first_page_only: bool = False) -> list[dict]:
        """Rows of every screener, stocks first."""
        rows, seen = [], {}
        for screener in SCREENERS:
            for row in self.download_screener(screener, first_page_only):
                if row['ticker'] in seen:
                    logger.warning(f'{row["ticker"]} is in both {seen[row["ticker"]]} and {screener}')
                seen.setdefault(row['ticker'], screener)
                rows.append(row)
        return rows


def run(out_file, client: Optional[FinvizClient] = None, first_page_only: bool = False) -> None:
    """Download everything and write it to `out_file`. Nothing is written if any step fails."""
    rows = (client or FinvizClient()).download_all(first_page_only)
    write_csv(rows, collect_columns(rows), out_file)
    logger.info('data written to file successfully')
