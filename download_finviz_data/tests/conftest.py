import csv
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from download_finviz_data.csvio import open_text

FIXTURES = Path(__file__).parent / 'fixtures'


def fixture_html(name: str) -> str:
    return (FIXTURES / name).read_text()


def write_old_format(path, header: list, rows: list) -> None:
    """Write a CSV exactly as the old HTML scraper did (every field quoted, CRLF)."""
    with open_text(path, 'w') as f:
        csv.writer(f, quotechar='"', quoting=csv.QUOTE_ALL).writerows([header, *rows])


def read_csv(path) -> list:
    with open_text(path, 'r') as f:
        return list(csv.reader(f))


def make_row(ticker: str, **numbers) -> dict:
    """Minimal row in the shape Finviz embeds in its screener page."""
    return {'text': {'ticker': ticker, 'company': f'{ticker} Inc'}, 'numbers': numbers}


def make_page(rows: list, total: int, first_row: int, page_size: int = 20) -> str:
    """Screener page HTML wrapping a payload, mirroring the real page structure."""
    payload = {'data': {'rows': rows, 'totalCount': total, 'firstRow': first_row, 'pageSize': page_size}}
    return (
        '<!DOCTYPE html><html><head><title>Stock Screener - Custom</title>'
        '<script>FinvizSettings = {};</script>'
        f'<script type="application/json">{json.dumps(payload)}</script>'
        '</head><body></body></html>'
    )


class FakeResponse:
    def __init__(self, text: str = '', status_code: int = 200):
        self.text = text
        self.status_code = status_code


class FakeSession:
    """
    Serves queued responses per (screener, r) and records every requested URL.
    A queued item may be a FakeResponse or an Exception instance (raised on get).
    """

    def __init__(self):
        self.queue = {}
        self.urls = []
        self.headers = {}

    def add(self, screener: str, r: int, *responses):
        self.queue.setdefault((screener, r), []).extend(responses)

    def get(self, url, **kwargs):
        self.urls.append(url)
        parsed = urlparse(url)
        key = (parsed.path.rsplit('/', 1)[-1], int(parse_qs(parsed.query)['r'][0]))
        item = self.queue[key].pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def sleeps():
    return []
