import logging

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'


def setup_logging(level: str) -> None:
    """Logging setup shared by the package's command-line entry points."""
    logging.basicConfig(level=level, format=LOG_FORMAT)
