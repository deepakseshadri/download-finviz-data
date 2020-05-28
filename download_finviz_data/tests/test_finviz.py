import logging
import unittest

from download_finviz_data.finviz import Finviz


logging.disable(logging.CRITICAL)


class TestFinviz(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.f = Finviz()

    def test_download_data(self):
        self.f.download_data(1)
        self.assertEqual(self.f.request_object.status_code, 200)

    def test_get_bsoup_object(self):
        self.f.generate_bsoup_object()

    def test_get_page_count(self):
        data = self.f.get_page_count()
        self.assertGreater(len(data), 1)

    def test_get_table_header(self):
        data = self.f.get_table_header()
        self.assertEqual(data[0], 'Ticker')
        self.assertEqual(data[-1], 'Earnings')

    def test_get_table_rows(self):
        data = self.f.get_table_rows()
        self.assertEqual(len(data), 20)


if __name__ == '__main__':
    unittest.main()
