import csv
from datetime import date

import pytest

from download_finviz_data import normalize, repair
from download_finviz_data.normalize import Kind, NormalizeError
from download_finviz_data.tests.conftest import read_csv as read, write_old_format as write_old

# Header variants found in the 210 old files (2025-11-27 .. 2026-09-29).
HEADER_V1 = [
    'Ticker', 'Company', 'Sector', 'Industry', 'Country', 'Market Cap', 'P/E', 'Fwd P/E', 'PEG', 'P/S', 'P/B', 'P/C',
    'P/FCF', 'Dividend', 'Payout Ratio', 'EPS', 'EPS This Y', 'EPS Next Y', 'EPS Past 5Y', 'EPS Next 5Y',
    'Sales Past 5Y', 'EPS Q/Q', 'Sales Q/Q', 'Outstanding', 'Float', 'Insider Own', 'Insider Trans', 'Inst Own',
    'Inst Trans', 'Short Float', 'Short Ratio', 'ROA', 'ROE', 'ROIC', 'Curr R', 'Quick R', 'LTDebt/Eq', 'Debt/Eq',
    'Gross M', 'Oper M', 'Profit M', 'Perf Week', 'Perf Month', 'Perf Quart', 'Perf Half', 'Perf Year', 'Perf YTD',
    'Beta', 'ATR', 'Volatility W', 'Volatility M', 'SMA20', 'SMA50', 'SMA200', '50D High', '50D Low', '52W High',
    '52W Low', 'RSI', 'Change from Open', 'Gap', 'Recom', 'Avg Volume', 'Rel Volume', 'Price', 'Change', 'Volume',
    'Earnings',
]
HEADER_V2 = [('Forward P/E' if h == 'Fwd P/E' else h) for h in HEADER_V1]
HEADER_V3 = [{'Change from Open': 'Change from Open %', 'Change': 'Change %'}.get(h, h) for h in HEADER_V2]

# Alcoa's row copied verbatim from 2026-09-29.csv (ticker repaired), in HEADER_V3 order.
ALCOA = [
    'AA', 'Alcoa Corp', 'Basic Materials', 'Aluminum', 'USA', '11.08B', '8.61', '6.94', '0.35', '0.80', '1.50',
    '7.35', '31.47', '0.95%', '9.02%', '4.88', '72.22%', '-6.89%', '-', '19.73%', '7.07%', '142.56%', '28.64%',
    '263.91M', '262.49M', '0.54%', '-1.85%', '83.62%', '0.88%', '4.57%', '2.35', '8.10%', '19.10%', '13.45%',
    '1.53', '0.92', '0.30', '0.30', '14.20%', '11.66%', '9.33%', '-6.13%', '-16.59%', '-19.51%', '-33.61%',
    '23.99%', '-21.02%', '1.73', '1.73', '2.91%', '3.31%', '-10.34%', '-12.15%', '-27.72%', '-23.76%', '0.53%',
    '-50.26%', '31.24%', '30.17', '-0.85%', '0.19%', '1.89', '5.11M', '0.67', '41.97', '-0.66%', '3,409,430',
    'Oct 15/a',
]


# --------------------------------------------------------------------------- column map

class TestColumnMap:
    @pytest.mark.parametrize('header', [HEADER_V1, HEADER_V2, HEADER_V3])
    def test_every_header_variant_is_fully_mapped(self, header):
        assert all(name in normalize.COLUMNS for name in header)

    def test_variants_map_to_the_same_columns(self):
        assert normalize.output_columns(HEADER_V1) == normalize.output_columns(HEADER_V3)

    def test_output_columns_use_new_finviz_keys(self):
        columns = normalize.output_columns(HEADER_V3)
        assert columns[:6] == ['ticker', 'company', 'sector', 'industry', 'country', 'marketCap']
        for key in ('forwardPE', 'dividendYield', 'sharesOutstanding2', 'sharesFloat', 'roi', 'netMargin',
                    'shortInterestShare', 'perf4w', 'perf13w', 'changeopen', 'averageVolume', 'change', 'volume'):
            assert key in columns

    def test_earnings_timing_follows_earnings_date(self):
        columns = normalize.output_columns(HEADER_V3)
        assert columns[-2:] == ['earningsDate', 'earningsTiming']

    def test_output_keys_are_unique(self):
        columns = normalize.output_columns(HEADER_V3)
        assert len(columns) == len(set(columns)) == 69

    def test_unknown_header_raises(self):
        with pytest.raises(NormalizeError, match='Mystery'):
            normalize.output_columns(['Ticker', 'Mystery'])


# --------------------------------------------------------------------------- convert

class TestConvert:
    @pytest.mark.parametrize('kind, value, expected', [
        (Kind.TEXT, 'Alcoa Corp', 'Alcoa Corp'),
        (Kind.NUMBER, '9.13', '9.13'),
        (Kind.NUMBER, '-1.2', '-1.2'),
        (Kind.NUMBER, '7', '7'),
        (Kind.PERCENT, '0.95%', '0.95'),
        (Kind.PERCENT, '-6.89%', '-6.89'),
        (Kind.MILLIONS, '11.08B', '11080'),
        (Kind.MILLIONS, '263.91M', '263.91'),
        (Kind.MILLIONS, '500.5K', '0.5005'),
        (Kind.MILLIONS, '1.5B', '1500'),
        (Kind.SHARES, '5.87M', '5870000'),
        (Kind.SHARES, '781.45K', '781450'),
        (Kind.SHARES, '1.2B', '1200000000'),
        (Kind.INTEGER, '4,633,710', '4633710'),
        (Kind.INTEGER, '1,234,567,890', '1234567890'),
        (Kind.INTEGER, '512', '512'),
    ])
    def test_values(self, kind, value, expected):
        assert normalize.convert(kind, value) == expected

    @pytest.mark.parametrize('kind', [Kind.NUMBER, Kind.PERCENT, Kind.MILLIONS, Kind.SHARES, Kind.INTEGER])
    @pytest.mark.parametrize('missing', ['-', ''])
    def test_missing_becomes_empty(self, kind, missing):
        assert normalize.convert(kind, missing) == ''

    @pytest.mark.parametrize('kind, value', [
        (Kind.NUMBER, '12%'),
        (Kind.NUMBER, '1.2M'),
        (Kind.PERCENT, '12'),
        (Kind.MILLIONS, '12'),
        (Kind.MILLIONS, '1.2T'),
        (Kind.SHARES, '12%'),
        (Kind.INTEGER, '1.5'),
        (Kind.INTEGER, '12,34'),
        (Kind.NUMBER, 'abc'),
    ])
    def test_unexpected_format_raises(self, kind, value):
        with pytest.raises(ValueError):
            normalize.convert(kind, value)


class TestEarnings:
    @pytest.mark.parametrize('value, file_day, expected', [
        ('Oct 15/a', date(2026, 9, 29), ('2026-10-15', 'a')),
        ('Aug 26/b', date(2026, 9, 29), ('2026-08-26', 'b')),
        ('Feb 9', date(2026, 3, 2), ('2026-02-09', '')),
        ('Jan 5/b', date(2026, 12, 30), ('2027-01-05', 'b')),
        ('Dec 28/a', date(2026, 1, 3), ('2025-12-28', 'a')),
        ('-', date(2026, 1, 3), ('', '')),
    ])
    def test_nearest_year_and_timing(self, value, file_day, expected):
        assert normalize.convert_earnings(value, file_day) == expected

    def test_feb_29_uses_nearest_leap_year(self):
        assert normalize.convert_earnings('Feb 29/a', date(2028, 3, 1)) == ('2028-02-29', 'a')

    @pytest.mark.parametrize('value', ['Oct 15/x', 'Foo 3', '2026-10-15', 'Oct'])
    def test_unexpected_format_raises(self, value):
        with pytest.raises(ValueError):
            normalize.convert_earnings(value, date(2026, 9, 29))


# --------------------------------------------------------------------------- normalize_table

class TestNormalizeTable:
    def test_real_alcoa_row(self):
        columns, rows = normalize.normalize_table(HEADER_V3, [ALCOA], date(2026, 9, 29))
        row = rows[0]
        assert row['ticker'] == 'AA' and row['company'] == 'Alcoa Corp'
        assert row['marketCap'] == '11080'
        assert row['dividendYield'] == '0.95'
        assert row['eps5Years'] == ''
        assert row['epsYoY1'] == '-6.89'
        assert row['sharesOutstanding2'] == '263.91'
        assert row['averageVolume'] == '5110000'
        assert row['volume'] == '3409430'
        assert row['change'] == '-0.66'
        assert (row['earningsDate'], row['earningsTiming']) == ('2026-10-15', 'a')
        assert set(row) == set(columns)

    def test_error_names_row_and_column(self):
        bad = list(ALCOA)
        bad[HEADER_V3.index('P/E')] = '9.13%'
        with pytest.raises(NormalizeError, match=r"row 2 .*'P/E'.*9\.13%"):
            normalize.normalize_table(HEADER_V3, [ALCOA, bad], date(2026, 9, 29))

    def test_short_row_raises(self):
        with pytest.raises(NormalizeError, match='row 1'):
            normalize.normalize_table(HEADER_V3, [ALCOA[:10]], date(2026, 9, 29))


# --------------------------------------------------------------------------- normalize_dir

@pytest.fixture
def data_dir(tmp_path):
    write_old(tmp_path / '2026-09-29.csv', HEADER_V3, [ALCOA])
    write_old(tmp_path / '2025-11-27.csv', HEADER_V1, [[('AA' if h == 'Ticker' else '-') for h in HEADER_V1]])
    return tmp_path


class TestNormalizeDir:
    def test_converts_every_old_file_in_place(self, data_dir):
        summary = normalize.normalize_dir(data_dir)
        assert summary == {'2025-11-27.csv': 'normalized', '2026-09-29.csv': 'normalized'}
        header, row = read(data_dir / '2026-09-29.csv')
        assert header == normalize.output_columns(HEADER_V3)
        assert dict(zip(header, row))['marketCap'] == '11080'

    def test_output_matches_new_download_dialect(self, data_dir):
        normalize.normalize_dir(data_dir)
        raw = (data_dir / '2026-09-29.csv').read_bytes()
        assert raw.startswith(b'ticker,company,sector,')
        assert b'"' not in raw.split(b'\r\n')[1]

    def test_originals_backed_up_byte_for_byte(self, data_dir):
        original = (data_dir / '2026-09-29.csv').read_bytes()
        normalize.normalize_dir(data_dir)
        assert (data_dir / normalize.BACKUP_DIR / '2026-09-29.csv').read_bytes() == original

    def test_rerun_skips_normalized_files(self, data_dir):
        normalize.normalize_dir(data_dir)
        before = (data_dir / '2026-09-29.csv').read_bytes()
        assert normalize.normalize_dir(data_dir) == {'2025-11-27.csv': 'skipped', '2026-09-29.csv': 'skipped'}
        assert (data_dir / '2026-09-29.csv').read_bytes() == before

    def test_new_downloads_and_other_files_are_left_alone(self, data_dir):
        with repair.open_text(data_dir / '2026-11-14.csv.gz', 'w') as f:
            csv.writer(f).writerows([['ticker', 'company', 'price'], ['AA', 'Alcoa Corp', '41.0']])
        (data_dir / 'notes.csv').write_text('Ticker,Company\n')
        summary = normalize.normalize_dir(data_dir)
        assert summary['2026-11-14.csv.gz'] == 'skipped'
        assert 'notes.csv' not in summary
        assert (data_dir / 'notes.csv').read_text() == 'Ticker,Company\n'

    def test_dry_run_writes_nothing(self, data_dir):
        before = {p.name: p.read_bytes() for p in data_dir.iterdir()}
        assert normalize.normalize_dir(data_dir, dry_run=True)['2026-09-29.csv'] == 'normalized'
        assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == before

    def test_bad_value_stops_with_file_named_and_leaves_it_untouched(self, data_dir):
        bad = list(ALCOA)
        bad[HEADER_V3.index('Price')] = 'n/a'
        write_old(data_dir / '2026-09-29.csv', HEADER_V3, [bad])
        original = (data_dir / '2026-09-29.csv').read_bytes()
        with pytest.raises(NormalizeError, match='2026-09-29.csv'):
            normalize.normalize_dir(data_dir)
        assert (data_dir / '2026-09-29.csv').read_bytes() == original

    def test_rerepaired_file_is_normalized_again_and_backup_refreshed(self, data_dir):
        """repair rewrites old-format files from its own backups; normalize must pick those up again."""
        normalize.normalize_dir(data_dir)
        updated = [('AA' if h == 'Ticker' else ('Alcoa Corporation' if h == 'Company' else v))
                   for h, v in zip(HEADER_V3, ALCOA)]
        write_old(data_dir / '2026-09-29.csv', HEADER_V3, [updated])
        assert normalize.normalize_dir(data_dir)['2026-09-29.csv'] == 'normalized'
        assert read(data_dir / '2026-09-29.csv')[1][1] == 'Alcoa Corporation'
        assert read(data_dir / normalize.BACKUP_DIR / '2026-09-29.csv')[1][1] == 'Alcoa Corporation'

    def test_normalized_files_still_work_as_repair_references(self, data_dir):
        normalize.normalize_dir(data_dir)
        _, rows, ticker_col, company_col = repair.read_table(data_dir / '2026-09-29.csv')
        assert (rows[0][ticker_col], rows[0][company_col]) == ('AA', 'Alcoa Corp')
