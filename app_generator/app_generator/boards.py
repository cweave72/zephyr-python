"""Board discovery for the app generator.

Boards come from two places:

  - out-of-tree roots under ``common/boards/<vendor>/<board>/board.yml``, which
    ``common/zephyr/module.yml`` registers via ``board_root: .``;
  - upstream Zephyr boards, which are far too numerous to enumerate, so only the
    ones this workspace actually uses are offered.

Each board carries a *net-type hint*: the transport that board normally uses.
The hint is only a default -- an explicit ``net_type`` answer always wins. It is
written into the generated ``boards/<board>.conf`` as
``CONFIG_APP_NET_TYPE_<X>=y``, which is what ``app_net_type_resolve()`` reads at
CMake configure time (see common/scripts/cmake/app_net_type.cmake).

``usb`` is deliberately never a hint: it needs native USB device support and is
opt-in per board.
"""
import logging
import os
import os.path as osp
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Upstream boards this workspace builds for. Keyed by the plain board name; the
# value is the fully qualified target Zephyr 4.0 wants for `west build -b`.
UPSTREAM_BOARDS = {
    "esp32_devkitc_wroom": "esp32_devkitc_wroom/esp32/procpu",
    "esp32c3_042_oled": "esp32c3_042_oled",
    "qemu_x86": "qemu_x86",
    "qemu_x86_64": "qemu_x86_64",
}

# Net-type hints. Anything not listed falls back to the app-wide net_type answer.
NET_TYPE_HINTS = {
    "esp32_devkitc_wroom": "wifi",
    "esp32c3_042_oled": "wifi",
    "esp32s3_matrix": "wifi",
    "esp32s3_qtpy": "wifi",
    "w55rp20_evb_pico": "eth",
    "qemu_x86": "serial",
    "qemu_x86_64": "serial",
}

# Boards whose wifi driver does its own DHCP and must be told not to when the
# app asks for a static address. See the IP addressing section of the plan.
ESP32_WIFI_AUTO_DHCP_BOARDS = {
    "esp32_devkitc_wroom", "esp32c3_042_oled", "esp32s3_matrix", "esp32s3_qtpy",
}


def workspace_base():
    """Locates the workspace root.

    Prefers WORKSPACE_BASE (set by workspace-env.sh, which common.mk sources);
    otherwise walks up from this file looking for the west manifest.
    """
    env = os.environ.get("WORKSPACE_BASE")
    if env and Path(env).is_dir():
        return Path(env)

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".west" / "config").exists():
            return parent
    raise RuntimeError(
        "Cannot locate the workspace root. Set WORKSPACE_BASE, or run from "
        "inside the west workspace.")


def discover_out_of_tree(base=None):
    """-> {board_name: qualified_target} for common/boards/<vendor>/<board>.

    The qualified target (e.g. ``esp32s3_matrix/esp32s3/procpu``) is read from
    the ``identifier`` field of the board's Twister yaml, NOT reconstructed from
    board.yml. board.yml lists the SoC but not its cpuclusters -- those come
    from the SoC definition in the HAL -- so reconstructing gives
    ``esp32s3_matrix/esp32s3``, which Zephyr 4.0 rejects. The identifier is
    what `west build -b` expects verbatim.
    """
    base = Path(base) if base else workspace_base()
    roots = base / "common" / "boards"
    found = {}
    if not roots.is_dir():
        logger.warning("No out-of-tree board root at %s", roots)
        return found

    for board_yml in sorted(roots.glob("*/*/board.yml")):
        bdir = board_yml.parent
        try:
            data = yaml.safe_load(board_yml.read_text()) or {}
        except yaml.YAMLError as e:
            logger.warning("Skipping %s: %s", board_yml, e)
            continue
        name = (data.get("board") or {}).get("name") or bdir.name

        target = None
        for cand in sorted(bdir.glob("*.yaml")):
            if cand.name == "board.yml":
                continue
            try:
                info = yaml.safe_load(cand.read_text()) or {}
            except yaml.YAMLError:
                continue
            ident = info.get("identifier")
            if ident:
                # Prefer an identifier naming this board; first wins otherwise.
                if target is None or ident.startswith(f"{name}/"):
                    target = ident
                if ident.startswith(f"{name}/"):
                    break

        if target is None:
            logger.warning(
                "%s: no board yaml with an 'identifier'; falling back to the "
                "bare board name, which may not be a valid build target.", bdir)
            target = name
        found[name] = target
    return found


def all_boards(base=None):
    """-> sorted list of (name, qualified_target, net_hint)."""
    boards = dict(UPSTREAM_BOARDS)
    boards.update(discover_out_of_tree(base))
    return [
        (name, target, NET_TYPE_HINTS.get(name))
        for name, target in sorted(boards.items())
    ]


def conf_basename(target):
    """-> the basename Zephyr looks for under boards/ for a build target.

    Zephyr's zephyr_build_string() (deps/zephyr/cmake/modules/extensions.cmake)
    joins the board with its qualifiers by "_", and additionally produces a
    SHORT form that drops the FIRST qualifier segment (the SoC). Application
    board files conventionally use the short form, which is what every existing
    app in this tree does:

        esp32s3_matrix/esp32s3/procpu    -> esp32s3_matrix_procpu
        esp32_devkitc_wroom/esp32/procpu -> esp32_devkitc_wroom_procpu
        w55rp20_evb_pico/rp2040          -> w55rp20_evb_pico
        esp32c3_042_oled                 -> esp32c3_042_oled

    Getting this wrong is silent: a board file named <board>.conf for a
    qualified target is simply never applied, and the build succeeds with the
    board's settings missing.
    """
    parts = target.split("/")
    board, qualifiers = parts[0], parts[1:]
    return "_".join([board] + qualifiers[1:])


def net_hint(board):
    """-> the board's usual transport, or None."""
    return NET_TYPE_HINTS.get(board)
