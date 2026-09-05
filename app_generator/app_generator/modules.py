"""Kconfig dependency parsing and closure for common/modules.

Why this exists: Kconfig ``depends on`` does NOT auto-enable a symbol -- it makes
it unselectable. Writing ``CONFIG_TCPSERVER=y`` into a fragment while
``CONFIG_TCPSOCKET`` is off leaves TCPSERVER disabled, and Zephyr reports it only
as a warning-shaped "assigned the value 'y' but got the value 'n'". Worse, the
module's ``zephyr_include_directories()`` call is inside ``if (CONFIG_<X>)``, so
a consumer that includes its header then fails with a bare "No such file".

So the generator resolves the closure itself and writes every required symbol
into conf/modules.conf explicitly.

This reads the tree rather than hardcoding a table, which is only trustworthy
because the Task 0 audit corrected the declarations first.
"""
import logging
import re
from pathlib import Path

from app_generator.boards import workspace_base

logger = logging.getLogger(__name__)

LOG_LEVELS = ["off", "err", "wrn", "inf", "dbg"]
DEFAULT_LOG_LEVEL = "inf"

# Modules offered in the selector. Value is the module directory name; the
# Kconfig symbol is resolved from the directory's Kconfig (see symbol_map()),
# because it is not always the dirname uppercased -- slip defines SLIP_FRAME.
SELECTABLE = [
    "MqttClient", "EchoServer", "NvParms", "Publisher",
    "TcpEcho", "TcpServer", "TraceModule", "UdpServer",
]

# EchoServer picks its transport via a Kconfig `choice`, which cannot be
# inferred -- the caller must answer it.
ECHOSERVER_TRANSPORTS = {
    "tcp": "ECHOSERVER_TRANSPORT_TCP",
    "udp": "ECHOSERVER_TRANSPORT_UDP",
}


def _modules_dir(base=None):
    base = Path(base) if base else workspace_base()
    return base / "common" / "modules"


def parse_kconfig(path):
    """Parse one Kconfig into {symbol: {'depends': [...], 'selects': [...]}}.

    Attribution is per enclosing `config` block. A file-wide grep would wrongly
    make Publisher look self-dependent, because PUBLISHER_MAX_STRING_SIZE
    legitimately declares `depends on PUBLISHER`.
    """
    out = {}
    cur = None
    if not path.exists():
        return out
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(?:config|menuconfig)\s+([A-Za-z0-9_]+)", line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, {"depends": [], "selects": []})
            continue
        # These end attribution of the current config block.
        if re.match(r"^(choice|endchoice|menu|endmenu)\b", line):
            cur = None
            continue
        if cur is None:
            continue
        m = re.match(r"^depends on\s+(.+)$", line)
        if m:
            out[cur]["depends"].extend(_symbols_in(m.group(1)))
            continue
        m = re.match(r"^select\s+(.+)$", line)
        if m:
            out[cur]["selects"].extend(_symbols_in(m.group(1)))
    return out


def _symbols_in(expr):
    """Pull bare symbol names out of a dependency expression.

    Deliberately simple: splits on boolean/grouping punctuation and keeps
    uppercase tokens. Conditional expressions (`if`, comparisons) are rare in
    these Kconfigs and a superset is safe here -- an extra CONFIG_X=y that is
    already implied costs nothing, whereas a missing one silently disables a
    module.
    """
    toks = re.split(r"[\s&|()!<>=]+", expr)
    drop = {"if", "y", "n", "m"}
    return [t for t in toks
            if t and t not in drop and re.fullmatch(r"[A-Z0-9_]+", t)]


def load(base=None):
    """-> (symbols, owner) where symbols maps SYMBOL -> deps dict, and owner
    maps SYMBOL -> module directory name."""
    mdir = _modules_dir(base)
    symbols, owner = {}, {}
    for d in sorted(p for p in mdir.iterdir() if p.is_dir()):
        parsed = parse_kconfig(d / "Kconfig")
        for sym, info in parsed.items():
            symbols[sym] = info
            owner[sym] = d.name
    return symbols, owner


def symbol_map(base=None):
    """-> {module_dir: main Kconfig symbol}. The main symbol is the first
    `config` in the module's Kconfig (slip -> SLIP_FRAME, not SLIP)."""
    mdir = _modules_dir(base)
    out = {}
    for d in sorted(p for p in mdir.iterdir() if p.is_dir()):
        kc = d / "Kconfig"
        if not kc.exists():
            continue
        m = re.search(r"^config\s+([A-Za-z0-9_]+)", kc.read_text(errors="replace"), re.M)
        if m:
            out[d.name] = m.group(1)
    return out


def closure(selected_symbols, base=None):
    """Transitively resolve `depends on` for the given symbols.

    Returns (all_symbols, added), where `all_symbols` includes the inputs and
    `added` is what the closure pulled in beyond them -- which is what the TUI
    shows under "Pulled in by deps".
    """
    symbols, owner = load(base)
    local = set(symbols)

    resolved, queue = set(), list(selected_symbols)
    while queue:
        sym = queue.pop()
        if sym in resolved:
            continue
        resolved.add(sym)
        info = symbols.get(sym)
        if info is None:
            # A Zephyr symbol (MQTT_LIB, FLASH, NETWORKING...). It has no entry
            # here, so it terminates the walk -- we cannot see Zephyr's own
            # dependency graph and do not try to.
            continue
        for dep in info["depends"] + info["selects"]:
            if dep not in resolved:
                queue.append(dep)

    added = sorted(resolved - set(selected_symbols))
    return sorted(resolved), added, local


def is_local(sym, base=None):
    symbols, _ = load(base)
    return sym in symbols
