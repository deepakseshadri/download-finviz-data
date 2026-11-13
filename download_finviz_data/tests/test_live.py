"""
Hits finviz.com for real. Excluded by default; run with: pytest -m live
"""
import pytest

from download_finviz_data.finviz import SCREENERS, FinvizClient

pytestmark = pytest.mark.live


@pytest.mark.parametrize('screener', SCREENERS)
def test_first_page_parses(screener):
    rows = FinvizClient().download_screener(screener, first_page_only=True)
    assert len(rows) == 20
    assert all(r['ticker'] for r in rows)
    assert len({r['ticker'] for r in rows}) == 20
