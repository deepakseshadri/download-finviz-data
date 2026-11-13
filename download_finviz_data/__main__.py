import argparse
import logging
import sys
from typing import Optional

from download_finviz_data.finviz import DEFAULT_DELAY, FinvizClient, FinvizError, run


logger = logging.getLogger(__name__)


def _non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f'must be >= 0, got {value}')
    return number


def parse_arguments(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Download all Finviz stock and ETF screener data to CSV')
    parser.add_argument(
        '-l', '--log-level',
        choices=['DEBUG', 'INFO', 'ERROR'],
        default='INFO',
        help='Logging level'
    )
    parser.add_argument(
        '-o', '--out-file',
        required=True,
        help='Output file (full path). Gzip-compressed when it ends in .gz'
    )
    parser.add_argument(
        '-t', '--test',
        action='store_true',
        help='Test script (downloads only the first page of each screener)'
    )
    parser.add_argument(
        '-d', '--delay',
        type=_non_negative_float,
        default=DEFAULT_DELAY,
        help=f'Base seconds between requests, stretched by a random +10-30%% (default {DEFAULT_DELAY})'
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_arguments(argv)
    logging.basicConfig(
        level=args.log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    logger.info('starting')
    try:
        run(args.out_file, client=FinvizClient(delay=args.delay), first_page_only=args.test)
    except FinvizError as err:
        logger.error(err)
        sys.exit(1)
    logger.info('done')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Goodbye!')
