import csv
from datetime import date

import pytest

from download_finviz_data import repair
from download_finviz_data.repair import Row
from download_finviz_data.tests.conftest import read_csv as read, write_old_format

OLD_HEADER = ['Ticker', 'Company', 'Sector', 'Price']


def write_old(path, rows, header=OLD_HEADER):
    write_old_format(path, header, rows)


def write_new(path, rows):
    """A file in the new downloader's format: lowercase Finviz keys, gzip."""
    with repair.open_text(path, 'w') as f:
        writer = csv.writer(f)
        writer.writerow(['ticker', 'company', 'price'])
        writer.writerows(rows)


# --------------------------------------------------------------------------- small helpers

class TestIsTruncated:
    def test_all_single_letters(self):
        assert repair.is_truncated(['A', 'A', 'B', 'Z'])

    def test_any_longer_ticker_means_not_truncated(self):
        assert not repair.is_truncated(['A', 'AA', 'B'])

    def test_empty_is_not_truncated(self):
        assert not repair.is_truncated([])


class TestReadTable:
    def test_old_format_columns_found_by_header(self, tmp_path):
        p = tmp_path / '2026-07-14.csv'
        write_old(p, [['AA', 'Alcoa Corp', 'Basic Materials', '39.74']])
        header, rows, ticker_col, company_col = repair.read_table(p)
        assert header == OLD_HEADER
        assert (ticker_col, company_col) == (0, 1)
        assert rows == [['AA', 'Alcoa Corp', 'Basic Materials', '39.74']]

    def test_new_format_gzip(self, tmp_path):
        p = tmp_path / '2026-11-14.csv.gz'
        write_new(p, [['AA', 'Alcoa Corp', '39.74']])
        _, rows, ticker_col, company_col = repair.read_table(p)
        assert rows[0][ticker_col] == 'AA' and rows[0][company_col] == 'Alcoa Corp'

    def test_missing_columns_raise(self, tmp_path):
        p = tmp_path / '2026-07-14.csv'
        write_old(p, [['x', 'y']], header=['Symbol', 'Name'])
        with pytest.raises(ValueError, match='ticker'):
            repair.read_table(p)


# --------------------------------------------------------------------------- reference index

class TestReferenceIndex:
    def test_records_date_span_per_company_and_ticker(self):
        index = repair.ReferenceIndex()
        index.add(date(2026, 6, 1), [('Alcoa Corp', 'AA')])
        index.add(date(2026, 7, 14), [('Alcoa Corp', 'AA')])
        assert index.spans['Alcoa Corp']['AA'] == (date(2026, 6, 1), date(2026, 7, 14))

    def test_candidates_filtered_by_first_letter(self):
        index = repair.ReferenceIndex()
        index.add(date(2026, 7, 14), [('AlphaEdge ETF', 'VLLU')])
        assert index.candidates('AlphaEdge ETF', 'A', date(2026, 9, 1)) == []
        assert index.candidates('AlphaEdge ETF', 'V', date(2026, 9, 1)) == ['VLLU']

    def test_unknown_company_has_no_candidates(self):
        assert repair.ReferenceIndex().candidates('Nobody', 'N', date(2026, 9, 1)) == []

    def test_share_classes_are_both_candidates_sorted(self):
        index = repair.ReferenceIndex()
        index.add(date(2026, 7, 14), [('Berkshire Hathaway Inc', 'BRK-B'), ('Berkshire Hathaway Inc', 'BRK-A')])
        assert index.candidates('Berkshire Hathaway Inc', 'B', date(2026, 8, 1)) == ['BRK-A', 'BRK-B']

    def test_nearest_in_time_wins_when_ticker_changed_within_same_letter(self):
        index = repair.ReferenceIndex()
        index.add(date(2026, 6, 1), [('Foo Inc', 'FOO')])
        index.add(date(2026, 7, 1), [('Foo Inc', 'FOOX')])
        index.add(date(2026, 11, 14), [('Foo Inc', 'FOOX')])
        assert index.candidates('Foo Inc', 'F', date(2026, 8, 1)) == ['FOOX']
        assert index.candidates('Foo Inc', 'F', date(2026, 6, 2)) == ['FOO']

    def test_inside_span_has_zero_distance(self):
        index = repair.ReferenceIndex()
        index.add(date(2026, 6, 1), [('Foo Inc', 'FOO')])
        index.add(date(2026, 11, 1), [('Foo Inc', 'FOO')])
        index.add(date(2026, 8, 16), [('Foo Inc', 'FOOB')])
        # Aug 15 lies inside FOO's Jun..Nov span (distance 0) and 1 day from FOOB's single sighting.
        assert index.candidates('Foo Inc', 'F', date(2026, 8, 15)) == ['FOO']


# --------------------------------------------------------------------------- resolve_tickers

def index_of(*pairs, when=date(2026, 7, 14)):
    index = repair.ReferenceIndex()
    index.add(when, pairs)
    return index


class TestResolveTickers:
    D = date(2026, 8, 1)

    def test_unique_match(self):
        rows = [Row('A', 'Alcoa Corp')]
        assert repair.resolve_tickers(rows, index_of(('Alcoa Corp', 'AA')), self.D) == [('AA', None)]

    def test_share_classes_assigned_in_row_order(self):
        rows = [Row('B', 'Berkshire Hathaway Inc'), Row('B', 'Berkshire Hathaway Inc')]
        index = index_of(('Berkshire Hathaway Inc', 'BRK-B'), ('Berkshire Hathaway Inc', 'BRK-A'))
        assert repair.resolve_tickers(rows, index, self.D) == [('BRK-A', None), ('BRK-B', None)]

    def test_not_found(self):
        assert repair.resolve_tickers([Row('N', 'New ETF')], index_of(), self.D) == [('N', 'not_found')]

    def test_letter_mismatch_is_not_found(self):
        rows = [Row('A', 'AlphaEdge ETF')]
        assert repair.resolve_tickers(rows, index_of(('AlphaEdge ETF', 'VLLU')), self.D) == [('A', 'not_found')]

    def test_more_candidates_than_rows_is_ambiguous(self):
        rows = [Row('B', 'Bel Fuse Inc')]
        index = index_of(('Bel Fuse Inc', 'BELFA'), ('Bel Fuse Inc', 'BELFB'))
        assert repair.resolve_tickers(rows, index, self.D) == [('B', 'ambiguous')]

    def test_more_rows_than_candidates_is_ambiguous(self):
        rows = [Row('B', 'Bel Fuse Inc'), Row('B', 'Bel Fuse Inc')]
        index = index_of(('Bel Fuse Inc', 'BELFA'))
        assert repair.resolve_tickers(rows, index, self.D) == [('B', 'ambiguous'), ('B', 'ambiguous')]

    def test_same_name_different_letters_resolved_separately(self):
        rows = [Row('A', 'Twin Co'), Row('T', 'Twin Co')]
        index = index_of(('Twin Co', 'ATW'), ('Twin Co', 'TWN'))
        assert repair.resolve_tickers(rows, index, self.D) == [('ATW', None), ('TWN', None)]

    def test_out_of_order_assignment_is_rejected(self):
        """Rows are sorted by ticker; a match that breaks the order is wrong and must not be kept."""
        rows = [Row('A', 'Aaa Co'), Row('A', 'Stale Name Co'), Row('A', 'Abc Co'), Row('A', 'Abd Co')]
        index = index_of(('Aaa Co', 'AAA'), ('Stale Name Co', 'AZZ'), ('Abc Co', 'ABC'), ('Abd Co', 'ABD'))
        assert repair.resolve_tickers(rows, index, self.D) == [
            ('AAA', None), ('A', 'order_conflict'), ('ABC', None), ('ABD', None)]

    def test_duplicate_assignment_is_rejected(self):
        rows = [Row('A', 'Old Name'), Row('A', 'New Name')]
        index = index_of(('Old Name', 'ABC'), ('New Name', 'ABC'))
        result = repair.resolve_tickers(rows, index, self.D)
        assert sorted(result) == [('A', 'order_conflict'), ('ABC', None)]


class TestKeepIncreasing:
    def test_already_increasing(self):
        assert repair.longest_increasing(['A', 'B', 'C']) == {0, 1, 2}

    def test_drops_minimum_to_restore_order(self):
        assert repair.longest_increasing(['A', 'Z', 'B', 'C']) == {0, 2, 3}

    def test_equal_values_are_not_increasing(self):
        assert len(repair.longest_increasing(['A', 'A'])) == 1


# --------------------------------------------------------------------------- repair_dir (end to end)

@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / 'finviz_data'
    d.mkdir()
    write_old(d / '2026-07-14.csv', [
        ['AA', 'Alcoa Corp', 'Basic Materials', '39.74'],
        ['BRK-A', 'Berkshire Hathaway Inc', 'Financial', '700000'],
        ['BRK-B', 'Berkshire Hathaway Inc', 'Financial', '466.10'],
    ])
    write_old(d / '2026-07-15.csv', [
        ['A', 'Alcoa Corp', 'Basic Materials', '40.01'],
        ['A', 'Brand New ETF', 'Financial', '25.00'],
        ['B', 'Berkshire Hathaway Inc', 'Financial', '701000'],
        ['B', 'Berkshire Hathaway Inc', 'Financial', '467.00'],
    ])
    return d


class TestRepairDir:
    def test_repairs_truncated_file_and_keeps_other_columns(self, data_dir):
        repair.repair_dir(data_dir)
        assert read(data_dir / '2026-07-15.csv') == [
            OLD_HEADER,
            ['AA', 'Alcoa Corp', 'Basic Materials', '40.01'],
            ['A', 'Brand New ETF', 'Financial', '25.00'],
            ['BRK-A', 'Berkshire Hathaway Inc', 'Financial', '701000'],
            ['BRK-B', 'Berkshire Hathaway Inc', 'Financial', '467.00'],
        ]

    def test_only_ticker_bytes_change(self, data_dir):
        original = (data_dir / '2026-07-15.csv').read_bytes()
        repair.repair_dir(data_dir)
        repaired = (data_dir / '2026-07-15.csv').read_bytes()
        assert repaired.replace(b'"AA"', b'"A"').replace(b'"BRK-A"', b'"B"').replace(b'"BRK-B"', b'"B"') == original
        assert repaired.count(b'\r\n') == original.count(b'\r\n')

    def test_original_backed_up_and_good_files_untouched(self, data_dir):
        good = (data_dir / '2026-07-14.csv').read_bytes()
        original = (data_dir / '2026-07-15.csv').read_bytes()
        repair.repair_dir(data_dir)
        assert (data_dir / repair.BACKUP_DIR / '2026-07-15.csv').read_bytes() == original
        assert (data_dir / '2026-07-14.csv').read_bytes() == good
        assert not (data_dir / repair.BACKUP_DIR / '2026-07-14.csv').exists()

    def test_report_lists_unresolved_rows(self, data_dir):
        repair.repair_dir(data_dir)
        assert read(data_dir / repair.REPORT_FILE) == [
            ['file', 'row', 'letter', 'company', 'reason'],
            ['2026-07-15.csv', '2', 'A', 'Brand New ETF', 'not_found'],
        ]

    def test_summary_counts(self, data_dir):
        summary = repair.repair_dir(data_dir)
        assert summary == {'2026-07-15.csv': {'rows': 4, 'resolved': 3, 'not_found': 1}}

    def test_dry_run_writes_nothing(self, data_dir):
        before = {p.name: p.read_bytes() for p in data_dir.iterdir()}
        summary = repair.repair_dir(data_dir, dry_run=True)
        assert summary['2026-07-15.csv']['resolved'] == 3
        assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == before

    def test_rerun_with_newer_reference_resolves_more(self, data_dir):
        repair.repair_dir(data_dir)
        write_new(data_dir / '2026-11-14.csv.gz', [['ANEW', 'Brand New ETF', '26.00'], ['AA', 'Alcoa Corp', '41.00']])
        summary = repair.repair_dir(data_dir)
        assert read(data_dir / '2026-07-15.csv')[2][0] == 'ANEW'
        assert summary['2026-07-15.csv'] == {'rows': 4, 'resolved': 4}
        assert read(data_dir / repair.REPORT_FILE) == [['file', 'row', 'letter', 'company', 'reason']]

    def test_rerun_reads_from_backup_and_never_uses_repaired_files_as_reference(self, data_dir):
        repair.repair_dir(data_dir)
        first = (data_dir / '2026-07-15.csv').read_bytes()
        repair.repair_dir(data_dir)
        assert (data_dir / '2026-07-15.csv').read_bytes() == first
        # The unresolved 'A' row must stay unresolved, not "resolve" to its own leftover letter.
        assert read(data_dir / repair.REPORT_FILE)[1][4] == 'not_found'

    def test_rerun_rebuilds_from_backup_not_from_the_current_file(self, data_dir):
        repair.repair_dir(data_dir)
        expected = read(data_dir / '2026-07-15.csv')
        damaged = [expected[0]] + [['ZZZ', *row[1:]] for row in expected[1:]]
        write_old(data_dir / '2026-07-15.csv', damaged[1:], header=damaged[0])
        repair.repair_dir(data_dir)
        assert read(data_dir / '2026-07-15.csv') == expected

    def test_no_truncated_files_is_a_no_op(self, tmp_path):
        write_old(tmp_path / '2026-07-14.csv', [['AA', 'Alcoa Corp', 'Basic Materials', '39.74']])
        assert repair.repair_dir(tmp_path) == {}
        assert not (tmp_path / repair.BACKUP_DIR).exists()
