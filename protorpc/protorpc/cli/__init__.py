# Common cli functions and helpers.
#
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

fmt_str = "[%(levelname)6s] (%(filename)s:%(lineno)s) %(message)s"


def setup_logging(rootlogger, level, logfile=None):
    """Sets up logging in cli apps.
    """

    rootlogger.setLevel(logging.DEBUG)

    if logfile:
        fh = logging.FileHandler(logfile, mode='w')
        fmt = logging.Formatter(fmt=fmt_str)

        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        rootlogger.addHandler(fh)

    con = Console()
    if con.is_terminal:
        ch = RichHandler(rich_tracebacks=True, show_time=False)
    else:
        ch = logging.StreamHandler()
        fmt = logging.Formatter(fmt=fmt_str)
        ch.setFormatter(fmt)

    ch.setLevel(loglevels[level])
    rootlogger.addHandler(ch)


def get_params(**kwargs):
    """Converts kwargs to Params class.
    """

    class Params:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    return Params(**kwargs)
