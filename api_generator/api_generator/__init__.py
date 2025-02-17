import logging
from rich.logging import RichHandler
from rich.console import Console

logger = logging.getLogger(__name__)

loglevels = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}


def setup_logging(rootlogger, level, logfile=None):

    rootlogger.setLevel(logging.DEBUG)

    if logfile:
        fh = logging.FileHandler(logfile, mode='w')
        fmt = logging.Formatter(
            fmt="%(asctime)s: [%(levelname)6s] %(name)s: %(message)s")

        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        rootlogger.addHandler(fh)

    con = Console()
    if con.is_terminal:
        ch = RichHandler(rich_tracebacks=True, show_time=False)
    else:
        ch = logging.StreamHandler()
        fmt = logging.Formatter(fmt="[%(levelname)6s] %(message)s")
        ch.setFormatter(fmt)

    ch.setLevel(loglevels[level])
    rootlogger.addHandler(ch)
