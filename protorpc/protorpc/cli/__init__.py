# Common cli functions and helpers.
#
import os
import os.path as osp
import json
import logging
from pathlib import Path
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

# Path to file for storing session state for cli apps.
CURRDIR = osp.abspath(os.curdir)
SESSION_STATE_FILE = '.cli_session_state'


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


def manage_session_state() -> bool:
    """Manages the session state for the cli app. This is used to determine
    if the cli app is being run in a new terminal session or not.
    Returns True if the cli's session state has changed since the last call.
    """
    session_state_path = osp.join(CURRDIR, SESSION_STATE_FILE)
    logger.debug(f"{session_state_path=}")

    # Read session state file, if it doesn't exist, return True.
    if not Path(session_state_path).exists():
        entry = dict(session_id=int(os.getppid()))
        logger.debug(f"Session state file {session_state_path} does not exist. "
                     "Creating with current session id.")
        with open(session_state_path, 'w') as f:
            json.dump(entry, f)
        return True

    # The session id is the parent process id of the cli app, which should be
    # unique for each session (shell process). If the cli is run repeatedly
    # in the same terminal, the session id will be the same, but if the cli
    # is run in a new terminal, the session id will be different.
    with open(session_state_path, 'r') as f:
        session_state_entry = json.load(f)

    try:
        saved_session_id = session_state_entry['session_id']
    except KeyError:
        # If the session state file is malformed, overwrite it with the current
        # session id and return True.
        logger.warning(f"Session state file {session_state_path} is malformed. "
                       "Overwriting with current session id.")
        entry = dict(session_id=int(os.getppid()))
        with open(session_state_path, 'w') as f:
            json.dump(entry, f)
        return True

    # Get the current session id and compare to the saved session id. If they
    # are different, overwrite the session state file with the current session
    # id and return True, otherwise return False.
    current_session_id = int(os.getppid())
    if saved_session_id != current_session_id:
        logger.debug(f"Session state has changed since last call. "
                     f"Saved session id: {saved_session_id}, "
                     f"Current session id: {current_session_id}. "
                     "Updating session state file.")
        entry = dict(session_id=current_session_id)
        with open(session_state_path, 'w') as f:
            json.dump(entry, f)
        return True

    logger.debug("Session state has not changed since last call.")
    return False


