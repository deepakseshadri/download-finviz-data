import argparse
import logging

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


def setup_logging(level: str) -> None:
    """Logging setup shared by the package's command-line entry points."""
    logging.basicConfig(level=level, format=LOG_FORMAT)


def data_dir_parser(description: str) -> argparse.ArgumentParser:
    """Arguments shared by the maintenance tools that rewrite a directory of downloads."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--data-dir', required=True, help='Directory of YYYY-MM-DD.csv[.gz] downloads')
    parser.add_argument('--dry-run', action='store_true', help='Report what would change; write nothing')
    parser.add_argument('-l', '--log-level', choices=['DEBUG', 'INFO', 'ERROR'], default='INFO')
    return parser
