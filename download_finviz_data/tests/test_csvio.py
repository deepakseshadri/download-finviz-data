from datetime import date

import pytest

from download_finviz_data import csvio


class TestFileDate:
    def test_plain_and_gzip_names(self, tmp_path):
        assert csvio.file_date(tmp_path / '2026-07-15.csv') == date(2026, 7, 15)
        assert csvio.file_date(tmp_path / '2026-11-14.csv.gz') == date(2026, 11, 14)

    def test_non_dated_names_are_none(self, tmp_path):
        assert csvio.file_date(tmp_path / 'notes.csv') is None
        assert csvio.file_date(tmp_path / '_repair_report.csv') is None
        assert csvio.file_date(tmp_path / '.2026-07-15.csv') is None


class TestWriteAtomic:
    def test_replaces_target_with_written_content(self, tmp_path):
        target = tmp_path / '2026-07-15.csv'
        target.write_text('old')
        csvio.write_atomic(target, lambda tmp: tmp.write_text('new'))
        assert target.read_text() == 'new'
        assert [p.name for p in tmp_path.iterdir()] == ['2026-07-15.csv']

    def test_temp_file_keeps_the_suffix(self, tmp_path):
        seen = []
        csvio.write_atomic(tmp_path / 'x.csv.gz', lambda tmp: (seen.append(tmp.name), tmp.write_text('')))
        assert seen[0].endswith('.csv.gz')

    def test_failed_write_leaves_target_untouched_and_no_temp(self, tmp_path):
        target = tmp_path / 'x.csv'
        target.write_text('original')

        def boom(tmp):
            tmp.write_text('partial')
            raise RuntimeError('disk full')

        with pytest.raises(RuntimeError):
            csvio.write_atomic(target, boom)
        assert target.read_text() == 'original'
        assert [p.name for p in tmp_path.iterdir()] == ['x.csv']
