import os
import os.path as osp
import importlib
import yaml
import logging
import typing as t
from pathlib import Path
from dataclasses import dataclass

from rich import inspect
from rich.console import Console
from rich.table import Table
from rich.pretty import Pretty
from rich.align import Align
from rich.text import Text

from protorpc import build_api
from protorpc.api import Request

# Import the frame header class local proto lib which should be in the
# path of the cli app calling this function.
from protorpcheader.lib import ProtoRpcHeader, CallsetInfo

logger = logging.getLogger(__name__)

CURRDIR = osp.abspath(os.curdir)
# Base path to location of protorpc registry files.
REGISTRY_BASE = f"{str(Path.home())}/.local/share/protorpc/registry"
DEVICE_CALLSET_FILE = osp.join(CURRDIR, '.cli_device_callsets')


@dataclass
class RegistryItem:
    """Data class representing a registry entry for a generated RPC API.
    """
    package: str
    module: str
    cls: str


def get_registry() -> t.List[RegistryItem]:
    """Reads and returns the registry entries for generated RPC APIs.

    A registry entry is a YAML file with the following keys:
        package: "myrpc.lib"  (the package name of the generated API)
        module: "rpc_name"    (the module name of the generated API)
        cls: "Callset"        (the class name of the generated API callset)

    Given the above, the class should be importable equivalently as:
        'from myrpc.lib.rpc_name import Callset'
    """
    if not Path(REGISTRY_BASE).exists():
        msg = (f"Registry base directory {REGISTRY_BASE} does not exist. "
                "No registry entries found.")
        logger.error(msg)
        raise Exception(msg)

    registry_entries = []
    for entry_file in Path(REGISTRY_BASE).glob("*.yaml"):
        with open(entry_file, 'r') as f:
            try:
                entry = yaml.safe_load(f)
                item = RegistryItem(**entry)
                registry_entries.append(item)
                logger.debug(f"Loaded registry entry from {entry_file}: {item}")
            except Exception as e:
                logger.warning(f"Failed to load registry entry from {entry_file}: {str(e)}. ")

    return registry_entries


def get_registry_table() -> Table:
    """Returns a table of registry entries for generated RPC APIs.
    """
    registry_list = get_registry()

    tbl = Table(title=f"Registry: {REGISTRY_BASE}",
                show_header=True,
                header_style="magenta")
    tbl.add_column(Align.center("Package"), justify="left")
    tbl.add_column(Align.center("Module"), justify="left")
    tbl.add_column(Align.center("Class"), justify="left")
    for entry in registry_list:
        row = []
        row.append(Text(entry.package, style="yellow"))
        row.append(Text(entry.module, style="green"))
        row.append(Text(entry.cls, style="cyan"))
        tbl.add_row(*row)
    return tbl


def load_callset_yaml(callset_yaml: str):
    """Loads callset.yaml file.
    """
    p = Path(callset_yaml)
    if not p.exists():
        logger.error(f"File {callset_yaml} does not exist.")
        return {}

    with p.open() as stream:
        d = yaml.safe_load(stream)
    return d


def callset_table(callset_info: t.List[CallsetInfo]) -> Table:
    """Formats callset information into a rich table."""

    table = Table(title="Server-side Callsets", show_header=True, header_style="magenta")
    table.add_column("Id", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Version")

    for cs in callset_info:
        table.add_row(Pretty(cs.id),
                      f"[yellow]{cs.name}[/yellow]",
                      f"[green]v{cs.ver_major}.{cs.ver_minor}.{cs.ver_patch}[/green]")

    return table


def refresh_callset_bindings(
    protocol,
    ip,
    port,
    hostname=None,
    print_table=False
) -> t.List[CallsetInfo]:
    """Queries the server for callset information and returns a list of
    CallsetInfo objects.
    """
    _, conn = build_api(
        ProtoRpcHeader,
        callsets=None,
        protocol=protocol,
        port=port,
        addr=ip,
        hostname=hostname)

    #inspect(req.reply)

    req = Request(conn, ProtoRpcHeader, callset_query=True)
    req.send_sync()
    conn.close()

    callset_info = req.reply.header.callset_info

    if print_table:
        con = Console()
        tbl = callset_table(callset_info)
        con.print(tbl)

    # Load the callset registry. This identifies corresponding bindings for
    # available callsets on the server.
    registry_list = get_registry()

    update_device_callset_file(ip, registry_list, callset_info)

    return callset_info


def update_device_callset_file(
    ip: str,
    registry: t.List[RegistryItem],
    device_callsets: t.List[CallsetInfo],
):
    """ Refreshes the callset bindings for the device at the given IP address.
    """
    devices = {}

    logger.info(f"Refreshing callset bindings for device at IP {ip}")
 
    pth = Path(DEVICE_CALLSET_FILE)
    if pth.exists():
        with pth.open() as f:
            try:
                devices = yaml.safe_load(f)
                logger.debug(f"Loaded device callsets from {DEVICE_CALLSET_FILE}: {devices}")
            except Exception as e:
                logger.warning(f"Failed to load device callsets from {DEVICE_CALLSET_FILE}: {str(e)}. ")
                devices = {}
    else:
        pth.touch()
        
    bindings = {}
    for callset in device_callsets:
        for registry_item in registry:
            if callset.name == registry_item.module:
                logger.info(f"Binding callset '{registry_item.module}' to id {callset.id}.")
                binding = {
                    "pkg": registry_item.package,
                    "mod": registry_item.module,
                    "cls": registry_item.cls,
                    "ver": { "major": callset.ver_major,
                             "minor": callset.ver_minor,
                             "patch": callset.ver_patch},
                }
                bindings[callset.id] = binding
                # Break out of inner loop once a match is found.
                break

    devices[ip] = bindings
    logger.info(f"Callset bindings for device at IP {ip}:")
    for _id in devices[ip]:
        logger.info(f"  {_id}: {devices[ip][_id]}")

    with pth.open('w') as f:
        yaml.dump(devices, f, indent=4)


def get_callset_bindings(ip: str) -> dict:
    """ Gets the callset bindings for the device with the given IP address.
    """
    pth = Path(osp.join(CURRDIR, DEVICE_CALLSET_FILE))
    pth_abs = str(pth.resolve())

    if not pth.exists():
        logger.error(f"Device callset file {DEVICE_CALLSET_FILE} "
                      "does not exist. No bindings found.")
        return {}

    with pth.open() as f:
        try:
            devices = yaml.safe_load(f)
            logger.debug(f"Loaded device callsets from {pth_abs}: {devices}")
        except Exception as e:
            logger.error(f"Failed to load device callsets from {pth_abs}: {str(e)}.")
            return {}

    if ip not in devices:
        logger.error(f"No callset bindings found for device at IP {ip}.")
        return {}

    return devices[ip]


def import_class(pkg_name: str, mod_name: str, cls_name: str):
    """Imports class from module.
    Returns the imported class object.
    """
    try:
        logger.debug(f"performing: from {pkg_name}.{mod_name} import {cls_name}")
        mod = importlib.import_module(f"{pkg_name}.{mod_name}")
        cls = getattr(mod, cls_name)
        return cls
    except (ImportError, AttributeError) as e:
        msg = f"Error importing class {cls_name} from {pkg_name}.{mod_name}: {str(e)}"
        logger.error(msg)
        raise e


def get_callsets(callset_dict: dict):
    """Import callsets and return a list of tuples
    [(callset_cls, callset_id), ...]
    Input callset_dict format:
        { id:
            {  pkg: <name>,
               mod: <name>,
               cls: <name>,
            },
          ...
        }
    """
    callsets = []
    for _id in callset_dict:
        name = callset_dict[_id]['mod']
        pkg_name = callset_dict[_id]['pkg']
        mod_name = callset_dict[_id]['mod']
        cls_name = callset_dict[_id]['cls']
        cls = import_class(pkg_name, mod_name, cls_name)
        logger.debug(f"Callset {name} [id={_id}] -> {pkg_name}.{mod_name}.{cls_name}")
        callsets.append((cls, _id, name))

    return callsets
