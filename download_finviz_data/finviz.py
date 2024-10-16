from bs4 import BeautifulSoup as BSoup
import csv
import logging
import re
import requests
import sys

from download_finviz_data import args


logger = logging.getLogger(__name__)


class Finviz:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({'User-Agent': 'Mozilla 5.10'})
        self.request_object = None
        self.bsoup_object = None

    def download_data(self, count):
        """
        Takes finviz page access integer and returns a beautifulsoup object
        """
        count = int(count)
        url = f'http://finviz.com/screener.ashx?v=151&r={count}&c=1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,' \
              f'21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,' \
              f'54,55,56,57,58,59,60,61,62,63,64,65,66,67,68'
        logger.debug(f'scraping page at {url}')
        try:
            self.request_object = self.s.get(url)
        except Exception as err_msg:
            logger.error(err_msg)
            sys.exit(1)

    def get_page_count(self):
        """
        Takes the first page of finviz screener URL and returns a list of page access integers
        """
        data = []
        page_count_object = self.bsoup_object.find('select', id='pageSelect')
        logger.debug(f'page count HTML raw object: {page_count_object}')
        for i in page_count_object.find_all('option'):
            data.append(int(i['value']))
        logger.debug(f'number of pages to query: {len(data)}')
        return data

    def get_table_header(self):
        """
        Get table header
        """
        table_header = self.bsoup_object.find_all('th', {'class': re.compile('table-header')})
        logger.debug(f'table header text: {table_header}')
        cleaned_table_header_data = [str(x.text.strip()) for x in table_header]
        logger.debug(f'cleaned table header data: {cleaned_table_header_data}')
        return cleaned_table_header_data

    def get_table_rows(self):
        """
        Get table rows
        """
        data = []
        table = self.bsoup_object.find('table', {'class': re.compile('screener_table')})
        table_rows_object = table.find_all('tr', {'class': re.compile('styled-row')})
        for row in table_rows_object:
            logger.debug(f'table row: {row}')
            table_row_cells = row.find_all('td')
            logger.debug(f'table row data: {table_row_cells}')
            extracted_data = []
            for x in table_row_cells:
                if x.a:
                    x = x.a
                if x.span:
                    x = x.span
                extracted_data.append(str(x.text.strip()))
            logger.debug(f'row extracted and cleaned data: {len(extracted_data)}')
            if len(extracted_data) != 68:
                logger.error(f'column count mismatch: expected 68, found only {len(extracted_data)} at '
                             f'symbol {extracted_data[0]}')
                sys.exit(1)
            data.append(extracted_data)
        logger.debug(f'length of data is: {len(data)}')
        logger.info(f'extracted data for symbols {data[0][0]} - {data[-1][0]}')
        return data

    def generate_bsoup_object(self):
        """
        Parses text from web pages using HTML parser and returns a BSoup object
        """
        self.bsoup_object = BSoup(self.request_object.text, 'html.parser')

    def run(self):
        res = list()
        self.download_data(1)
        self.generate_bsoup_object()
        count = self.get_page_count()
        res.append(self.get_table_header())
        rows = self.get_table_rows()
        res += rows
        if not args.test:
            for i in count[1:]:
                self.download_data(i)
                self.generate_bsoup_object()
                rows = self.get_table_rows()
                res += rows

        if args.out_file:
            write_to_file(res, args.out_file)


def write_to_file(data, fn):
    """
    Write data to a file in CSV format
    """
    logger.info(f'writing to file {fn}')
    try:
        with open(fn, 'w') as f:
            writer = csv.writer(f, quotechar='"', quoting=csv.QUOTE_ALL)
            writer.writerows(data)
    except Exception as err:
        logger.error(err)
        sys.exit(1)
    logger.info('data written to file successfully')
