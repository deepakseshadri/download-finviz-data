"""CSV file I/O and data-directory conventions shared by the downloader and the maintenance tools."""

import gzip
import os
import re
from datetime import date
from pathlib import Path
from typing import Callable, Optional

_DATED_FILE = re.compile(r'^(\d{4}-\d{2}-\d{2})\.csv(\.gz)?$')


def open_text(path, mode: str):
    """Open a CSV for text 'r' or 'w' (newline='' as the csv module requires); gzip when the path ends in .gz."""
    path = Path(path)
    opener = gzip.open if path.suffix.lower() == '.gz' else open
    return opener(path, f'{mode}t', newline='')


def file_date(path) -> Optional[date]:
    """Date encoded in a download's file name (YYYY-MM-DD.csv[.gz]), else None."""
    match = _DATED_FILE.match(Path(path).name)
    return date.fromisoformat(match.group(1)) if match else None


def write_atomic(path, write: Callable[[Path], None]) -> None:
    """Call write(tmp) on a hidden sibling with the same suffix, then swap it in; the target is never half-written."""
    path = Path(path)
    tmp = path.with_name(f'.{path.name}')
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
