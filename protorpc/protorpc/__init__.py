import logging
from rich.logging import RichHandler
from rich.console import Console
from rich import inspect
from typing import List

from protorpc.api import Api, FrameDict, parse_callset_fields
from protorpc.connection.udp_connection import UdpConnection
from protorpc.connection.tcp_connection import TcpConnection


logger = logging.getLogger(__name__)

loglevels = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

fmt_str = "[%(levelname)6s] (%(filename)s:%(lineno)s) %(message)s"


class ProtoRpcException(Exception):
    pass


def setup_logging(rootlogger, level, logfile=None):

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


def build_api(header_cls, callsets: List[tuple], **kwargs):
    """Builds the RPC api from the frame class.
    header_cls : Class for the RPC header.
    callsets: [{callset_cls, id}, ...]
    Accepts the following kwargs:
    protocol : ['tcp', 'udp']
    port     : some integer
    addr     : server IP address (optional).
    hostname : server hostname (optional)
    """
    protocol = kwargs.pop('protocol', 'tcp')

    supported_prots = ['tcp', 'udp']

    if protocol not in supported_prots:
        raise ProtoRpcException(f"Unsupported protocol: {protocol}. "
                                f"Must be {supported_prots}.")

    connectCls = {'tcp': TcpConnection, 'udp': UdpConnection}[protocol]
    logger.debug(f"Using connection class={connectCls.__name__}")
    try:
        conn = connectCls(**kwargs)
        conn.connect()
    except Exception as e:
        logger.error(f"build_api: Connection error ({protocol}).")
        raise ProtoRpcException(e)

    api = {}

    # Process each callset provided.
    for callset_cls, id_ in callsets:
        parse_callset_fields(callset_cls, cs_id=id_)

    logger.debug(f"FrameDict={FrameDict}")

    for callset in FrameDict:
        logger.debug(f"Adding api for callset={callset}")
        api[callset] = Api(header_cls, FrameDict[callset], conn)

    return api, conn
