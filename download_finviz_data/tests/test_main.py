import pytest

from download_finviz_data import __main__ as cli
from download_finviz_data.finviz import DEFAULT_DELAY, FinvizError


class TestParseArguments:
    def test_defaults(self):
        args = cli.parse_arguments(['-o', '/tmp/x.csv.gz'])
        assert args.out_file == '/tmp/x.csv.gz'
        assert args.delay == DEFAULT_DELAY
        assert args.test is False
        assert args.log_level == 'INFO'

    def test_custom_delay(self):
        assert cli.parse_arguments(['-o', 'x.csv', '--delay', '5.5']).delay == 5.5

    def test_negative_delay_rejected(self):
        with pytest.raises(SystemExit):
            cli.parse_arguments(['-o', 'x.csv', '--delay', '-1'])

    def test_out_file_required(self):
        with pytest.raises(SystemExit):
            cli.parse_arguments([])


class TestMain:
    def test_passes_arguments_to_run(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cli, 'run', lambda out, client, first_page_only: calls.append(
            (out, client.delay, first_page_only)))
        cli.main(['-o', 'out.csv.gz', '-t', '--delay', '4'])
        assert calls == [('out.csv.gz', 4.0, True)]

    def test_finviz_error_exits_non_zero(self, monkeypatch, caplog):
        def boom(*args, **kwargs):
            raise FinvizError('page layout changed')
        monkeypatch.setattr(cli, 'run', boom)
        with pytest.raises(SystemExit) as exc:
            cli.main(['-o', 'out.csv.gz'])
        assert exc.value.code == 1
        assert 'page layout changed' in caplog.text
