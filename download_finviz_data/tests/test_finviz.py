import csv
import gzip
import logging
import random
from pathlib import Path

import pytest
import requests

from download_finviz_data import finviz
from download_finviz_data.finviz import FinvizClient, FinvizError
from download_finviz_data.tests.conftest import FakeResponse, fixture_html, make_page, make_row


# --------------------------------------------------------------------------- jittered

class TestJittered:
    def test_uses_low_bound(self):
        assert finviz.jittered(10, uniform=lambda lo, hi: lo) == pytest.approx(11.0)

    def test_uses_high_bound(self):
        assert finviz.jittered(10, uniform=lambda lo, hi: hi) == pytest.approx(13.0)

    def test_draws_from_plus_10_to_30_percent(self):
        calls = []
        finviz.jittered(3, uniform=lambda lo, hi: calls.append((lo, hi)) or lo)
        assert calls == [(1.10, 1.30)]

    def test_never_shorter_than_base_with_real_randomness(self):
        random.seed(1234)
        samples = [finviz.jittered(3) for _ in range(2000)]
        assert min(samples) >= 3 * 1.10
        assert max(samples) <= 3 * 1.30

    def test_zero_base_is_zero(self):
        assert finviz.jittered(0) == 0


# --------------------------------------------------------------------------- build_url

class TestBuildUrl:
    def test_targets_new_screener_path_directly(self):
        url = finviz.build_url('etf', 21)
        assert url.startswith('https://finviz.com/screener/etf?')
        assert 'v=151' in url
        assert '&r=21' in url

    def test_requests_every_column_id_up_to_300(self):
        url = finviz.build_url('stocks', 1)
        columns = url.split('c=', 1)[1].split('&')[0].split(',')
        assert columns == [str(i) for i in range(0, 301)]

    def test_rejects_unknown_screener(self):
        with pytest.raises(ValueError):
            finviz.build_url('futures', 1)


# --------------------------------------------------------------------------- is_challenge

class TestIsChallenge:
    def test_detects_real_cloudflare_challenge_page(self):
        assert finviz.is_challenge(fixture_html('challenge.html'))

    def test_real_screener_page_is_not_a_challenge(self):
        assert not finviz.is_challenge(fixture_html('stocks_page.html'))


# --------------------------------------------------------------------------- extract_payload

class TestExtractPayload:
    def test_real_stocks_page(self):
        data = finviz.extract_payload(fixture_html('stocks_page.html'))
        assert data['totalCount'] == 5613
        assert data['pageSize'] == 20
        assert data['firstRow'] == 0
        assert [r['text']['ticker'] for r in data['rows']] == ['A', 'AA', 'AAC']

    def test_real_etf_page(self):
        data = finviz.extract_payload(fixture_html('etf_page.html'))
        assert data['totalCount'] == 6150
        assert data['rows'][0]['text']['ticker'] == 'AAA'

    def test_skips_unrelated_json_scripts(self):
        html = '<script type="application/json">{"ads": 1}</script>' + make_page([make_row('X')], 1, 0)
        assert finviz.extract_payload(html)['rows'][0]['text']['ticker'] == 'X'

    def test_page_without_payload_raises(self):
        with pytest.raises(FinvizError, match='payload'):
            finviz.extract_payload('<html><head><title>Redesigned</title></head></html>')

    def test_challenge_page_raises(self):
        with pytest.raises(FinvizError):
            finviz.extract_payload(fixture_html('challenge.html'))

    def test_malformed_json_raises_finviz_error(self):
        with pytest.raises(FinvizError, match='payload'):
            finviz.extract_payload('<script type="application/json">{not json</script>')


# --------------------------------------------------------------------------- flatten_row

class TestFlattenRow:
    def test_merges_text_and_numbers_without_prefix(self):
        row = {'text': {'ticker': 'A', 'sector': 'Healthcare'}, 'numbers': {'PE': 34.25}}
        assert finviz.flatten_row(row) == {'ticker': 'A', 'sector': 'Healthcare', 'PE': 34.25}

    def test_prefixes_other_nested_groups(self):
        row = {'text': {'ticker': 'A'}, 'range52w': {'low': 108.35, 'high': 182.99}}
        assert finviz.flatten_row(row) == {'ticker': 'A', 'range52w.low': 108.35, 'range52w.high': 182.99}

    def test_keeps_scalar_groups_as_is(self):
        assert finviz.flatten_row({'text': {'ticker': 'A'}, 'order': 3}) == {'ticker': 'A', 'order': 3}

    def test_rounds_floats_to_4_decimals(self):
        row = {'text': {'ticker': 'A'}, 'numbers': {'price': 173.9600067138672, 'tiny': 0.00123456}}
        flat = finviz.flatten_row(row)
        assert flat['price'] == 173.96
        assert flat['tiny'] == 0.0012

    def test_leaves_ints_and_strings_untouched(self):
        row = {'text': {'ticker': 'A', 'optionable': 'No'}, 'numbers': {'volume': 1347542}}
        flat = finviz.flatten_row(row)
        assert flat['volume'] == 1347542 and isinstance(flat['volume'], int)
        assert flat['optionable'] == 'No'

    def test_key_collision_between_groups_raises(self):
        row = {'text': {'ticker': 'ZZZ', 'price': 'n/a'}, 'numbers': {'price': 1.0}}
        with pytest.raises(FinvizError, match=r"'price'.*ZZZ"):
            finviz.flatten_row(row)

    def test_real_stock_row_has_all_fields_finviz_returned(self):
        row = finviz.extract_payload(fixture_html('stocks_page.html'))['rows'][0]
        flat = finviz.flatten_row(row)
        expected = len(row['text']) + len(row['numbers']) + len(row['range52w'])
        assert len(flat) == expected
        assert flat['ticker'] == 'A'
        assert flat['company'] == 'Agilent Technologies Inc'
        assert 'range52w.low' in flat


# --------------------------------------------------------------------------- page_offsets

class TestPageOffsets:
    def test_exact_multiple(self):
        assert finviz.page_offsets(60, 20) == [1, 21, 41]

    def test_partial_last_page(self):
        assert finviz.page_offsets(41, 20) == [1, 21, 41]

    def test_single_partial_page(self):
        assert finviz.page_offsets(5, 20) == [1]

    def test_real_totals(self):
        offsets = finviz.page_offsets(5613, 20)
        assert len(offsets) == 281
        assert offsets[-1] == 5601

    def test_zero_total_has_no_pages(self):
        assert finviz.page_offsets(0, 20) == []

    def test_invalid_page_size_raises(self):
        with pytest.raises(FinvizError):
            finviz.page_offsets(100, 0)


# --------------------------------------------------------------------------- collect_columns

class TestCollectColumns:
    def test_ticker_and_company_lead_then_first_seen_order(self):
        rows = [{'PE': 1, 'company': 'A Inc', 'ticker': 'A'}, {'ticker': 'B', 'aum': 5, 'PE': 2}]
        assert finviz.collect_columns(rows) == ['ticker', 'company', 'PE', 'aum']

    def test_union_of_stock_and_etf_fields(self):
        stock = finviz.flatten_row(finviz.extract_payload(fixture_html('stocks_page.html'))['rows'][0])
        etf = finviz.flatten_row(finviz.extract_payload(fixture_html('etf_page.html'))['rows'][0])
        columns = finviz.collect_columns([stock, etf])
        assert set(columns) == set(stock) | set(etf)
        assert len(columns) == len(set(columns))
        assert 'e.NetExpenseRatio' in columns and 'PE' in columns


# --------------------------------------------------------------------------- validate_rows

class TestValidateRows:
    def test_valid_rows_pass_through(self):
        rows = [{'ticker': 'A'}, {'ticker': 'B'}]
        assert finviz.validate_rows(rows, 2, 'stocks') == rows

    def test_empty_ticker_raises(self):
        with pytest.raises(FinvizError, match='ticker'):
            finviz.validate_rows([{'ticker': 'A'}, {'ticker': ''}], 2, 'stocks')

    def test_missing_ticker_raises(self):
        with pytest.raises(FinvizError, match='ticker'):
            finviz.validate_rows([{'company': 'X'}], 1, 'stocks')

    def test_duplicate_tickers_dropped_keeping_first_with_warning(self, caplog):
        rows = [{'ticker': 'A', 'v': 1}] + [{'ticker': f'T{i}'} for i in range(200)] + [{'ticker': 'A', 'v': 2}]
        with caplog.at_level(logging.WARNING):
            result = finviz.validate_rows(rows, 201, 'stocks')
        assert [r for r in result if r['ticker'] == 'A'] == [{'ticker': 'A', 'v': 1}]
        assert len(result) == 201
        assert 'duplicate' in caplog.text

    def test_small_count_drift_warns(self, caplog):
        rows = [{'ticker': f'T{i}'} for i in range(999)]
        with caplog.at_level(logging.WARNING):
            assert len(finviz.validate_rows(rows, 1000, 'stocks')) == 999
        assert '999' in caplog.text and '1000' in caplog.text

    def test_large_shortfall_raises(self):
        """Catches gross corruption such as the 2026-07 ticker truncation (26 unique of ~11k)."""
        rows = [{'ticker': t} for t in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' for _ in range(400)]
        with pytest.raises(FinvizError, match='26'):
            finviz.validate_rows(rows, 10400, 'stocks')

    def test_no_expected_total_skips_count_check(self):
        assert len(finviz.validate_rows([{'ticker': 'A'}], None, 'stocks')) == 1


# --------------------------------------------------------------------------- write_csv

class TestWriteCsv:
    ROWS = [{'ticker': 'A', 'PE': 34.25, 'note': 'a, "b"'}, {'ticker': 'B', 'aum': 5}]
    COLUMNS = ['ticker', 'PE', 'note', 'aum']
    EXPECTED = [COLUMNS, ['A', '34.25', 'a, "b"', ''], ['B', '', '', '5']]

    def test_plain_csv(self, tmp_path):
        path = tmp_path / 'out.csv'
        finviz.write_csv(self.ROWS, self.COLUMNS, path)
        with open(path, newline='') as f:
            assert list(csv.reader(f)) == self.EXPECTED

    def test_gzip_when_path_ends_in_gz(self, tmp_path):
        path = tmp_path / 'out.csv.gz'
        finviz.write_csv(self.ROWS, self.COLUMNS, path)
        assert path.read_bytes()[:2] == b'\x1f\x8b'
        with gzip.open(path, 'rt', newline='') as f:
            assert list(csv.reader(f)) == self.EXPECTED

    def test_gzip_suffix_is_case_insensitive(self, tmp_path):
        path = tmp_path / 'out.CSV.GZ'
        finviz.write_csv(self.ROWS, self.COLUMNS, path)
        assert path.read_bytes()[:2] == b'\x1f\x8b'

    def test_no_blank_lines_between_rows(self, tmp_path):
        path = tmp_path / 'out.csv'
        finviz.write_csv(self.ROWS, self.COLUMNS, path)
        assert b'\r\r\n' not in path.read_bytes()
        assert path.read_bytes().count(b'\n') == 3

    def test_unwritable_path_raises_finviz_error(self, tmp_path):
        with pytest.raises(FinvizError):
            finviz.write_csv(self.ROWS, self.COLUMNS, tmp_path / 'missing_dir' / 'out.csv')


# --------------------------------------------------------------------------- FinvizClient

MINUTE = 60


class TestPoliteness:
    """The agreed request schedule; changing these numbers should be a deliberate decision."""

    def test_default_delay_is_10_seconds(self):
        assert finviz.DEFAULT_DELAY == 10.0

    def test_backoff_schedule_is_15_30_30_30_minutes(self):
        assert finviz.BACKOFF_DELAYS == (15 * MINUTE, 30 * MINUTE, 30 * MINUTE, 30 * MINUTE)

    def test_total_backoff_budget_is_4_hours(self):
        assert finviz.MAX_TOTAL_BACKOFF == 4 * 60 * MINUTE


def high_uniform(lo, hi):
    return hi


def make_client(session, sleeps, delay=3.0):
    return FinvizClient(session=session, delay=delay, sleep=sleeps.append, uniform=high_uniform)


class TestClientFetch:
    def test_returns_payload_data(self, session, sleeps):
        session.add('stocks', 1, FakeResponse(fixture_html('stocks_page.html')))
        data = make_client(session, sleeps).fetch('stocks', 1)
        assert data['totalCount'] == 5613

    def test_no_sleep_before_first_request_then_jittered_delay(self, session, sleeps):
        session.add('stocks', 1, FakeResponse(make_page([make_row('A')], 1, 0)))
        session.add('etf', 1, FakeResponse(make_page([make_row('B')], 1, 0)))
        client = make_client(session, sleeps, delay=3.0)
        client.fetch('stocks', 1)
        assert sleeps == []
        client.fetch('etf', 1)
        assert sleeps == [pytest.approx(3.0 * 1.30)]

    def test_retries_challenge_with_jittered_backoff(self, session, sleeps):
        challenge = FakeResponse(fixture_html('challenge.html'), 403)
        session.add('stocks', 1, challenge, challenge, FakeResponse(make_page([make_row('A')], 1, 0)))
        data = make_client(session, sleeps).fetch('stocks', 1)
        assert data['rows'][0]['text']['ticker'] == 'A'
        assert sleeps == [pytest.approx(15 * MINUTE * 1.30), pytest.approx(30 * MINUTE * 1.30)]

    def test_challenge_served_with_200_is_still_retried(self, session, sleeps):
        session.add('stocks', 1, FakeResponse(fixture_html('challenge.html'), 200),
                    FakeResponse(make_page([make_row('A')], 1, 0)))
        assert make_client(session, sleeps).fetch('stocks', 1)['rows']
        assert len(session.urls) == 2

    def test_gives_up_after_all_backoffs(self, session, sleeps):
        challenge = FakeResponse(fixture_html('challenge.html'), 403)
        session.add('stocks', 1, *[challenge] * 5)
        with pytest.raises(FinvizError, match='challenge'):
            make_client(session, sleeps).fetch('stocks', 1)
        assert len(session.urls) == 5
        assert sleeps == [pytest.approx(m * MINUTE * 1.30) for m in (15, 30, 30, 30)]

    def test_total_backoff_budget_of_4_hours_ends_the_run(self, session, sleeps):
        """
        One fully-failed page waits (15+30+30+30) * 1.3 = 136.5 min. On the next page the third wait would push the
        total to 136.5 + 19.5 + 39 + 39 = 234 (ok), and the fourth to 273 > 240 min, so the run stops before it.
        """
        challenge = FakeResponse(fixture_html('challenge.html'), 403)
        session.add('stocks', 1, *[challenge] * 4, FakeResponse(make_page([make_row('A')], 1, 0)))
        session.add('etf', 1, *[challenge] * 5)
        client = make_client(session, sleeps, delay=0)
        client.fetch('stocks', 1)
        with pytest.raises(FinvizError, match='4.0 hours'):
            client.fetch('etf', 1)
        backoffs = [s for s in sleeps if s > 0]
        assert sum(backoffs) <= 4 * 60 * MINUTE
        assert len(backoffs) == 7
        assert len(session.urls) == 9

    @pytest.mark.parametrize('status', [429, 500, 502, 503, 504])
    def test_retries_transient_http_status(self, session, sleeps, status):
        session.add('stocks', 1, FakeResponse('busy', status), FakeResponse(make_page([make_row('A')], 1, 0)))
        assert make_client(session, sleeps).fetch('stocks', 1)['rows']

    def test_retries_connection_error(self, session, sleeps):
        session.add('stocks', 1, requests.ConnectionError('reset'), FakeResponse(make_page([make_row('A')], 1, 0)))
        assert make_client(session, sleeps).fetch('stocks', 1)['rows']

    def test_non_retryable_status_fails_immediately(self, session, sleeps):
        session.add('stocks', 1, FakeResponse('nope', 404))
        with pytest.raises(FinvizError, match='404'):
            make_client(session, sleeps).fetch('stocks', 1)
        assert len(session.urls) == 1
        assert sleeps == []

    def test_requests_built_url(self, session, sleeps):
        session.add('etf', 21, FakeResponse(make_page([make_row('A')], 21, 20)))
        make_client(session, sleeps).fetch('etf', 21)
        assert session.urls == [finviz.build_url('etf', 21)]


class TestClientDownloadScreener:
    @staticmethod
    def add_pages(session, screener, tickers, page_size=2):
        total = len(tickers)
        for r in finviz.page_offsets(total, page_size):
            chunk = tickers[r - 1:r - 1 + page_size]
            session.add(screener, r, FakeResponse(make_page([make_row(t) for t in chunk], total, r - 1, page_size)))

    def test_fetches_every_page_including_partial_last(self, session, sleeps):
        self.add_pages(session, 'stocks', ['A', 'B', 'C', 'D', 'E'])
        rows = make_client(session, sleeps).download_screener('stocks')
        assert [r['ticker'] for r in rows] == ['A', 'B', 'C', 'D', 'E']
        assert len(session.urls) == 3
        assert len(sleeps) == 2

    def test_rows_are_flattened(self, session, sleeps):
        session.add('stocks', 1, FakeResponse(make_page([make_row('A', price=1.23456)], 1, 0)))
        assert make_client(session, sleeps).download_screener('stocks') == [
            {'ticker': 'A', 'company': 'A Inc', 'price': 1.2346}]

    def test_first_page_only(self, session, sleeps):
        self.add_pages(session, 'stocks', ['A', 'B', 'C', 'D', 'E'])
        rows = make_client(session, sleeps).download_screener('stocks', first_page_only=True)
        assert [r['ticker'] for r in rows] == ['A', 'B']
        assert len(session.urls) == 1

    def test_empty_page_raises(self, session, sleeps):
        session.add('stocks', 1, FakeResponse(make_page([make_row('A'), make_row('B')], 4, 0, 2)))
        session.add('stocks', 3, FakeResponse(make_page([], 4, 2, 2)))
        with pytest.raises(FinvizError, match='no rows'):
            make_client(session, sleeps).download_screener('stocks')

    def test_wrong_first_row_raises(self, session, sleeps):
        """Guards against Finviz ignoring the r= offset and silently serving page 1 again."""
        session.add('stocks', 1, FakeResponse(make_page([make_row('A'), make_row('B')], 4, 0, 2)))
        session.add('stocks', 3, FakeResponse(make_page([make_row('A'), make_row('B')], 4, 0, 2)))
        with pytest.raises(FinvizError, match='firstRow'):
            make_client(session, sleeps).download_screener('stocks')

    def test_total_count_change_warns_once_and_uses_latest(self, session, sleeps, caplog):
        page = lambda tickers, total, first_row: FakeResponse(make_page([make_row(t) for t in tickers], total, first_row, 2))
        session.add('stocks', 1, page(['A', 'B'], 6, 0))
        session.add('stocks', 3, page(['C', 'D'], 5, 2))
        session.add('stocks', 5, page(['E'], 5, 4))
        with caplog.at_level(logging.WARNING):
            rows = make_client(session, sleeps).download_screener('stocks')
        assert len(rows) == 5
        assert caplog.text.count('totalCount changed') == 1
        assert 'Finviz reported' not in caplog.text

    def test_logs_progress_every_50_pages(self, session, sleeps, caplog):
        self.add_pages(session, 'stocks', [f'T{i}' for i in range(100)], page_size=1)
        with caplog.at_level(logging.INFO, logger='download_finviz_data.finviz'):
            make_client(session, sleeps).download_screener('stocks')
        progress = [r.getMessage() for r in caplog.records if 'pages done' in r.getMessage()]
        assert progress == ['stocks: 50/100 pages done', 'stocks: 100/100 pages done']

    def test_empty_screener_returns_nothing(self, session, sleeps):
        session.add('stocks', 1, FakeResponse(make_page([], 0, 0)))
        assert make_client(session, sleeps).download_screener('stocks') == []


class TestClientDownloadAll:
    def test_combines_stocks_then_etfs(self, session, sleeps):
        session.add('stocks', 1, FakeResponse(make_page([make_row('A')], 1, 0)))
        session.add('etf', 1, FakeResponse(make_page([make_row('SPY')], 1, 0)))
        rows = make_client(session, sleeps).download_all()
        assert [r['ticker'] for r in rows] == ['A', 'SPY']

    def test_ticker_in_both_screeners_is_kept_with_warning(self, session, sleeps, caplog):
        session.add('stocks', 1, FakeResponse(make_page([make_row('X')], 1, 0)))
        session.add('etf', 1, FakeResponse(make_page([make_row('X')], 1, 0)))
        with caplog.at_level(logging.WARNING):
            rows = make_client(session, sleeps).download_all()
        assert len(rows) == 2
        assert 'X' in caplog.text


# --------------------------------------------------------------------------- run

class TestRun:
    def test_writes_gzipped_csv_of_all_screeners(self, session, sleeps, tmp_path):
        session.add('stocks', 1, FakeResponse(make_page([make_row('A', PE=10.5)], 1, 0)))
        session.add('etf', 1, FakeResponse(make_page([make_row('SPY', aum=99)], 1, 0)))
        out = tmp_path / 'finviz.csv.gz'
        finviz.run(out, client=make_client(session, sleeps))
        with gzip.open(out, 'rt', newline='') as f:
            assert list(csv.reader(f)) == [
                ['ticker', 'company', 'PE', 'aum'],
                ['A', 'A Inc', '10.5', ''],
                ['SPY', 'SPY Inc', '', '99'],
            ]

    def test_crash_mid_write_keeps_previous_file_and_leaves_no_partial(self, session, sleeps, tmp_path, monkeypatch):
        session.add('stocks', 1, FakeResponse(make_page([make_row('A')], 1, 0)))
        session.add('etf', 1, FakeResponse(make_page([make_row('SPY')], 1, 0)))
        out = tmp_path / 'finviz.csv'
        out.write_text('previous run')

        def crash(rows, columns, path):
            Path(path).write_text('ticker,comp')
            raise FinvizError('disk full')

        monkeypatch.setattr(finviz, 'write_csv', crash)
        with pytest.raises(FinvizError):
            finviz.run(out, client=make_client(session, sleeps))
        assert out.read_text() == 'previous run'
        assert [p.name for p in tmp_path.iterdir()] == ['finviz.csv']

    def test_failure_writes_no_file(self, session, sleeps, tmp_path):
        session.add('stocks', 1, FakeResponse('<html>redesign</html>'))
        out = tmp_path / 'finviz.csv.gz'
        with pytest.raises(FinvizError):
            finviz.run(out, client=make_client(session, sleeps))
        assert not out.exists()
