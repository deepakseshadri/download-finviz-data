"""CSV file I/O shared by the downloader and the maintenance tools."""

import gzip
from pathlib import Path


def open_text(path, mode: str):
    """Open a CSV for text 'r' or 'w' (newline='' as the csv module requires); gzip when the path ends in .gz."""
    path = Path(path)
    opener = gzip.open if path.suffix.lower() == '.gz' else open
    return opener(path, f'{mode}t', newline='')
