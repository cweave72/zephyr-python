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


# Boards whose own devicetree already provides a "led0" alias. For these the
# app must NOT redefine it -- the board knows its own LED.
BOARDS_WITH_LED0 = {
    "esp32s3_qtpy",
    "w55rp20_evb_pico",
}

# For boards with no led0 alias, the GPIO the onboard LED sits on. Taken from
# applications/blinky, which is the reference for LED handling in this tree.
# A board absent from both this map and BOARDS_WITH_LED0 gets a commented
# template in its overlay instead of a guess -- a wrong pin silently drives
# nothing, or drives something else.
LED0_GPIO = {
    "esp32_devkitc_wroom": ("gpio0", 2),
    "esp32s3_matrix": ("gpio0", 1),
}


# Flash layouts for the FsApi littlefs partition. The generator puts the
# partition at the top of flash and ends the code partition where it starts.
# Only boards verified on hardware are listed. Any other board gets a commented
# template in its overlay; FsApi then stops the build until it is filled in.
# A guessed layout could overlap the image or run past the real end of flash:
# the esp32s3 devicetrees declare 8 MiB, and some of those modules have 4 MiB.
#
#   flash_size     -- bytes of flash, as the board devicetree declares it
#   erase_block    -- erase block size in bytes; the fs size must be a multiple
#   flash_node     -- nodelabel of the flash device node
#   code_partition -- nodelabel of the partition the image runs from
#   code_start     -- offset of that partition
#   min_code_size  -- smallest code partition app_gen allows, in bytes
FS_LAYOUTS = {
    "w55rp20_evb_pico": {
        "flash_size": 2 * 1024 * 1024,
        "erase_block": 4096,
        "flash_node": "flash0",
        "code_partition": "code_partition",
        "code_start": 0x100,
        "min_code_size": 512 * 1024,
    },
}

DEFAULT_FS_SIZE_KB = 128

# littlefs needs 2 blocks for the root directory and more for any file.
MIN_FS_BLOCKS = 8


def fs_layout(board):
    """-> the FsApi flash layout of a board, or None if it is not known."""
    return FS_LAYOUTS.get(board)


def fs_size_errors(fs_size_kb, board_list):
    """-> list of reasons why fs_size_kb does not fit the given boards."""
    errs = []
    size = fs_size_kb * 1024
    for board in board_list:
        lay = fs_layout(board)
        if lay is None:
            continue
        eb = lay["erase_block"]
        if size % eb:
            errs.append(f"{board}: fs size {fs_size_kb} KiB is not a multiple "
                        f"of the {eb // 1024} KiB erase block.")
        if size < MIN_FS_BLOCKS * eb:
            errs.append(f"{board}: fs size must be at least "
                        f"{MIN_FS_BLOCKS * eb // 1024} KiB.")
        max_size = lay["flash_size"] - lay["code_start"] - lay["min_code_size"]
        max_size -= max_size % eb
        if size > max_size:
            errs.append(f"{board}: fs size must be at most {max_size // 1024} "
                        f"KiB, to leave {lay['min_code_size'] // 1024} KiB "
                        f"for code.")
    return errs


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
        "inside the west workspace. app_gen needs the full workspace: it "
        "reads the module and board data from the 'common' repository and "
        "writes the new application to the 'applications' repository. A "
        "standalone zephyr-python checkout cannot supply these.")


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
