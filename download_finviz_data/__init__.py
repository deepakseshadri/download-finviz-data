import argparse
import logging


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-l', '--log-level',
        choices=['DEBUG', 'INFO', 'ERROR'],
        default='INFO',
        help='Logging level'
    )
    parser.add_argument(
        '-o', '--out-file',
        help='Write the output to a file.  Provide full path'
    )
    parser.add_argument(
        '-t', '--test',
        action='store_true',
        help='Test script (downloads only the first page)'
    )
    return parser.parse_args()


args = parse_arguments()

logging.basicConfig(
    level=args.log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
