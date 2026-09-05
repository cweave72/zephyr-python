"""Answer assembly, Copier invocation, and the file manifest.

Division of labour with the Copier template:

  - The template owns everything whose *set of files* is fixed: CMakeLists.txt,
    Kconfig, prj.conf, conf/*, src/*. Conditional files use Copier's
    empty-rendered-filename idiom.
  - This module owns the per-board files, because the board set is discovered
    from common/boards and so cannot be enumerated in the template.

`plan_files()` is the single description of what a given set of answers
produces. Both `app_gen new --dry-run` and the TUI manifest pane call it, so the
preview cannot drift from the real output; test_manifest_matches() asserts it
against a real generation.
"""
import logging
import shutil
from pathlib import Path

from app_generator import boards as boards_mod
from app_generator import modules as modules_mod

logger = logging.getLogger(__name__)

# The Copier source is the zephyr-common repo ROOT, not the template directory
# inside it. Copier only treats a source as version-controlled when the path it
# is handed is a git repo root (copier._vcs.is_git_repo_root), and it records
# the template commit in .copier-answers.yml only for such sources -- which is
# what `copier update` needs to diff against. copier.yml therefore sits at the
# repo root with `_subdirectory: templates/app/template`.
TEMPLATE_SUBPATH = Path("common")


def template_path(base=None):
    base = Path(base) if base else boards_mod.workspace_base()
    return base / TEMPLATE_SUBPATH


def default_dest(app_name, base=None):
    base = Path(base) if base else boards_mod.workspace_base()
    return base / "applications" / app_name


def resolve_modules(selected, echoserver_transport="udp", base=None):
    """Resolve the Kconfig closure for the selected modules.

    `selected` is {module_name: log_level}.

    Returns a dict with:
      local   -- local module symbols to write (selected + pulled in)
      zephyr  -- Zephyr symbols the closure requires
      added   -- what the closure added beyond what was ticked (for the TUI)
      levels  -- {symbol: level} for the ticked modules only
    """
    symmap = modules_mod.symbol_map(base)
    picked = []
    levels = {}
    for name, level in (selected or {}).items():
        sym = symmap.get(name)
        if sym is None:
            logger.warning("Unknown module %r; ignoring.", name)
            continue
        picked.append(sym)
        levels[sym] = level or modules_mod.DEFAULT_LOG_LEVEL

    # EchoServer's transport is a Kconfig `choice`, which cannot be inferred.
    if "EchoServer" in (selected or {}):
        t = modules_mod.ECHOSERVER_TRANSPORTS.get(echoserver_transport)
        if t:
            picked.append(t)

    all_syms, added, local_syms = modules_mod.closure(picked, base)
    return {
        "local": sorted(s for s in all_syms if s in local_syms),
        "zephyr": sorted(s for s in all_syms if s not in local_syms),
        "added": added,
        "levels": levels,
    }


def build_answers(*, app_name, description="A Zephyr application.",
                  net_type="wifi", ip_mode="dhcp",
                  ipv4_addr="192.168.1.15", ipv4_mask="255.255.255.0",
                  ipv4_gw="192.168.1.1", use_rpc=True, use_tracing=False,
                  use_nv=None, use_shell=False, use_led=True, modules=None,
                  echoserver_transport="udp", board_list=None, base=None):
    """Normalize CLI/TUI input into the answer set the template expects."""
    if net_type == "none":
        # RPC needs a network; silently dropping it would be worse than saying so.
        if use_rpc:
            logger.info("net_type=none: disabling RPC (it requires networking).")
        use_rpc = False
    if use_nv is None:
        use_nv = (net_type == "wifi")

    modules = dict(modules or {})
    # Selecting TraceModule implies the tracing feature: TRACEMODULE depends on
    # TRACERAM, which conf/tracing.conf is what enables.
    if "TraceModule" in modules:
        use_tracing = True

    return {
        "app_name": app_name,
        "description": description,
        "net_type": net_type,
        "ip_mode": ip_mode if net_type != "none" else "dhcp",
        "ipv4_addr": ipv4_addr,
        "ipv4_mask": ipv4_mask,
        "ipv4_gw": ipv4_gw,
        "use_rpc": use_rpc,
        "use_tracing": use_tracing,
        "use_nv": use_nv,
        "use_shell": use_shell,
        "use_led": use_led,
        "modules": modules,
        "echoserver_transport": echoserver_transport,
        "module_symbols": resolve_modules(modules, echoserver_transport, base),
        "board_list": sorted(board_list or []),
    }


def modules_conf(answers, base=None):
    """-> the text of conf/modules.conf.

    Written here rather than by the template for two reasons: the template
    cannot parse Kconfig, and Copier does not persist computed answers (it
    omits both "_"-prefixed and `when: false` values from
    .copier-answers.yml), so a template that depended on one could not be
    re-rendered by `copier update`. Generating it here also means an update
    always reflects the CURRENT common/modules Kconfig.
    """
    syms = answers["module_symbols"]
    out = [
        "# Enabled modules, with their Kconfig dependency closure resolved by",
        "# app_gen.",
        "#",
        "# The closure is written out in full because Kconfig 'depends on' does",
        "# not auto-enable: a symbol whose dependency is unmet is silently",
        "# dropped, and the module's headers then vanish from the include path.",
        "",
    ]
    for sym in syms["local"]:
        out.append(f"CONFIG_{sym}=y")
    if syms["zephyr"]:
        out += ["", "# Pulled in by the closure (Zephyr subsystems)."]
        out += [f"CONFIG_{sym}=y" for sym in syms["zephyr"]]
    if syms["levels"]:
        out += ["", "# Per-module log levels."]
        out += [f"CONFIG_{sym}_LOG_LEVEL_{lvl.upper()}=y"
                for sym, lvl in syms["levels"].items()]
    return "\n".join(out).rstrip() + "\n"


def _boards_on_disk(dest, base=None):
    """-> board names an existing app supports, from its boards/*.conf files.

    The board list is not stored in .copier-answers.yml (it is a private "_"
    answer), so on update it is recovered from disk. Files are named with
    Zephyr's short build string, so map back through conf_basename().
    """
    out = set()
    bdir = Path(dest) / "boards"
    if not bdir.is_dir():
        return out
    stems = {p.stem for p in bdir.glob("*.conf")}
    for name, target, _hint in boards_mod.all_boards(base):
        if boards_mod.conf_basename(target) in stems:
            out.add(name)
    return out


def board_files(answers, base=None):
    """-> {relative_path: content} for boards/<board>.{conf,overlay}.

    Always emits an overlay, even an empty one: common.mk computes
    BOARDS := $(notdir $(wildcard boards/*.overlay)), so a board supported by a
    .conf alone never shows up in `make appboards`.
    """
    out = {}
    net_type = answers["net_type"]
    static_ip = answers["net_type"] != "none" and answers["ip_mode"] == "static"

    targets = {name: target for name, target, _ in boards_mod.all_boards(base)}
    for board in answers["board_list"]:
        # File name must be what Zephyr looks for, or it is silently ignored.
        stem = boards_mod.conf_basename(targets.get(board, board))
        lines = []
        if net_type != "none":
            hint = boards_mod.net_hint(board)
            declared = net_type
            lines += [
                "# Network transport for this board. This single declaration drives both",
                "# the Kconfig symbol and which conf/ fragment the build applies (see",
                "# CMakeLists.txt and common/scripts/cmake/app_net_type.cmake).",
            ]
            if hint and hint != net_type:
                lines.append(
                    f"# NOTE: this board normally uses '{hint}' networking; the app was")
                lines.append(
                    f"#       generated for '{net_type}'. Change this line to switch it.")
            lines.append(f"CONFIG_APP_NET_TYPE_{declared.upper()}=y")
            lines.append("")

        if (net_type == "wifi"
                and board in boards_mod.ESP32_WIFI_AUTO_DHCP_BOARDS):
            if static_ip:
                lines += [
                    "# The esp32 wifi driver runs its own DHCP client, which would fight",
                    "# the static address configured in conf/ip.conf.",
                    "CONFIG_ESP32_WIFI_STA_AUTO_DHCPV4=n",
                ]
            else:
                lines.append("CONFIG_ESP32_WIFI_STA_AUTO_DHCPV4=y")
            lines += ["CONFIG_WIFI_ESP32=y", "CONFIG_ESP32_WIFI_STA_RECONNECT=y", ""]

        out[f"boards/{stem}.conf"] = "\n".join(lines).rstrip() + "\n"
        out[f"boards/{stem}.overlay"] = _overlay(
            board, stem, net_type, answers.get("use_led", False))
    return out


def _led_overlay(board):
    """Devicetree fragment supplying the led0 alias, or "" when unnecessary.

    Three cases:
      - the board already defines led0 -> nothing, redefining it would fight
        the board's own devicetree;
      - we know the GPIO from applications/blinky -> emit a working node;
      - we do not -> emit a commented template. A guessed pin would silently
        drive nothing, or drive some other peripheral.
    """
    if board in boards_mod.BOARDS_WITH_LED0:
        return ("/* This board defines the led0 alias in its own devicetree, so\n"
                " * the application does not need to. */\n")

    pin = boards_mod.LED0_GPIO.get(board)
    if pin:
        port, num = pin
        return (
            f"/* Onboard LED. Pin taken from applications/blinky. */\n"
            f"/ {{\n"
            f"\tleds {{\n"
            f"\t\tcompatible = \"gpio-leds\";\n"
            f"\t\tmyled0: led_0 {{\n"
            f"\t\t\tgpios = <&{port} {num} GPIO_ACTIVE_HIGH>;\n"
            f"\t\t\tlabel = \"Onboard LED 0\";\n"
            f"\t\t}};\n"
            f"\t}};\n"
            f"\n"
            f"\taliases {{\n"
            f"\t\tled0 = &myled0;\n"
            f"\t}};\n"
            f"}};\n"
        )

    return (
        "/* This board has no led0 alias and the generator does not know which\n"
        " * GPIO its LED is on, so fill in the pin below to enable LED support.\n"
        " * Until then app_led_init() logs a warning and the LED calls are\n"
        " * no-ops -- the app still builds and runs.\n"
        " *\n"
        " * / {\n"
        " *     leds {\n"
        " *         compatible = \"gpio-leds\";\n"
        " *         myled0: led_0 {\n"
        " *             gpios = <&gpio0 0 GPIO_ACTIVE_HIGH>;\n"
        " *             label = \"Onboard LED 0\";\n"
        " *         };\n"
        " *     };\n"
        " *     aliases {\n"
        " *         led0 = &myled0;\n"
        " *     };\n"
        " * };\n"
        " */\n"
    )


def _overlay(board, stem, net_type, use_led=False):
    """Devicetree overlay for one board.

    Always emitted, even when it has no nodes: common.mk computes
    BOARDS := $(notdir $(wildcard boards/*.overlay)), so a board with no overlay
    never appears in `make appboards`.
    """
    head = (
        f"/*\n"
        f" * Devicetree overlay for {stem}.\n"
        f" */\n\n"
    )

    led = _led_overlay(board) if use_led else ""

    if net_type == "wifi" and board in boards_mod.ESP32_WIFI_AUTO_DHCP_BOARDS:
        # Not optional. CONFIG_WIFI_ESP32 is
        #   depends on DT_HAS_ESPRESSIF_ESP32_WIFI_ENABLED
        # so without this node the wifi driver cannot be selected at all -- and
        # because it is also `default y`, enabling the node is all that is
        # needed. Leaving it out builds cleanly, with no warning, and produces
        # an image with no wifi driver.
        return head + led + ("\n" if led else "") + (
            "/* Required: the esp32 wifi driver depends on this node being\n"
            " * enabled (CONFIG_WIFI_ESP32 depends on\n"
            " * DT_HAS_ESPRESSIF_ESP32_WIFI_ENABLED). Without it the driver is\n"
            " * silently absent from the image. */\n"
            "&wifi {\n"
            "\tstatus = \"okay\";\n"
            "};\n"
        )

    if net_type == "serial":
        # Which UART carries the SLIP link is board wiring, not something that
        # can be inferred -- so leave it explicit rather than guess and produce
        # a build that fails deep in the devicetree
        # (__device_dts_ord_DT_CHOSEN_zephyr_uart_pipe_ORD undeclared).
        return head + led + ("\n" if led else "") + (
            "/* REQUIRED for serial networking: nominate the UART that carries\n"
            " * the SLIP link. Which UART depends on your wiring, so it cannot\n"
            " * be generated. Without it the build fails with\n"
            " * '__device_dts_ord_DT_CHOSEN_zephyr_uart_pipe_ORD undeclared'.\n"
            " *\n"
            " * / {\n"
            " *     chosen {\n"
            " *         zephyr,uart-pipe = &uart2;\n"
            " *     };\n"
            " * };\n"
            " *\n"
            " * &uart2 {\n"
            " *     status = \"okay\";\n"
            " * };\n"
            " */\n"
        )

    if led:
        return head + led

    return head + (
        "/* Typically: pin/alias definitions the app needs (led0, a sensor\n"
        " * node), enabling peripherals with status = \"okay\", or repointing\n"
        " * chosen nodes such as zephyr,console / zephyr,shell-uart. */\n"
    )


    return out


def plan_files(answers, base=None):
    """-> sorted list of repo-relative paths this answer set will produce.

    Mirrors the template's conditional-filename rules. Kept beside them
    deliberately; test_manifest_matches() asserts the two agree.
    """
    net = answers["net_type"]
    files = [
        "CMakeLists.txt", "Kconfig", "Makefile", "README.md", "prj.conf",
        ".copier-answers.yml",
        "conf/kernel.conf", "conf/modules.conf",
        "src/app.h", "src/main.c",
    ]
    if net != "none":
        files += ["conf/net.conf", "conf/ip.conf",
                  "src/net_ip.c", "src/net_ip.h"]
    if answers["use_nv"] and net != "none":
        files.append("conf/nv.conf")
    if net == "wifi":
        files += ["conf/wifi.conf", "src/net_wifi.c"]
    elif net == "serial":
        files += ["conf/serial_net.conf", "src/net_wired.c",
                  "src/net_l4.c", "src/net_l4.h"]
    elif net == "eth":
        files += ["conf/wired_eth_net.conf", "src/net_wired.c",
                  "src/net_l4.c", "src/net_l4.h"]
    elif net == "usb":
        files += ["conf/usb_net.conf", "src/net_usb.c",
                  "src/net_l4.c", "src/net_l4.h"]
    if answers["use_rpc"]:
        files += ["conf/rpc.conf", "src/rpc.c"]
    if answers["use_tracing"]:
        files += ["conf/tracing.conf", "conf/tracemodule.conf", "src/trace.c"]
    if answers.get("use_led"):
        files += ["conf/led.conf", "src/led.c", "src/led.h"]
    for mod in answers["modules"]:
        if mod != "TraceModule":       # config-only, no _init(), no source
            files.append(f"src/{mod}.c")
    files += list(board_files(answers, base))
    return sorted(files)


def run(answers, dest, base=None, pretend=False, overwrite=False):
    """Generate the application. Returns the list of paths written."""
    from copier import run_copy

    dest = Path(dest)
    written = plan_files(answers, base)
    if pretend:
        return written

    run_copy(
        str(template_path(base)),
        str(dest),
        data=answers,
        defaults=True,
        unsafe=True,
        overwrite=overwrite,
        quiet=True,
    )
    generated = dict(board_files(answers, base))
    generated["conf/modules.conf"] = modules_conf(answers, base)
    for rel, content in generated.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return written


class UpdateNotPossible(Exception):
    """Raised when `copier update` cannot run, with actionable guidance."""


def update(dest, base=None):
    """Re-apply template changes to an app generated earlier.

    Copier's update needs two things that plain generation does not:

      1. a CLEAN git working tree at the destination, and
      2. a VERSIONED template -- the answers file must record a `_commit`,
         which Copier only writes when the template source is a git repo it can
         `git describe`. An untracked or untagged template records only
         `_src_path`, and update has no old version to diff against.

    Generation works fine without either; update is the opt-in extra.
    """
    import yaml
    from copier import run_update
    from copier.errors import UserMessageError

    # Copier does not persist answers whose names start with "_" -- it treats
    # them as private -- so the computed module_symbols is absent from
    # .copier-answers.yml and conf/modules.conf would fail to render on update.
    # Recompute it from the stored user answers. Recomputing rather than
    # persisting is deliberate: the dependency closure should reflect the
    # CURRENT common/modules Kconfig, so an update after a Kconfig fix picks up
    # newly declared dependencies.
    answers_path = Path(dest) / ".copier-answers.yml"

    try:
        # defaults=True: an update may introduce questions the app predates
        # (use_led did exactly that). Without it Copier tries to prompt and
        # fails outright in a non-interactive session.
        run_update(str(dest), defaults=True, overwrite=True,
                   unsafe=True, quiet=True)
    except UserMessageError as e:
        msg = str(e)
        if "old template references" in msg:
            raise UpdateNotPossible(
                f"{msg}\n\n"
                "The template is not versioned yet. Copier records a template "
                "commit only when it can 'git describe' the source. Fix with, "
                "in the 'common' repo:\n"
                "    git add templates/app && git commit -m 'Add app template'\n"
                "    git tag app-template-v1\n"
                "Apps generated after that can be updated; ones generated "
                "before will need '_commit' added to .copier-answers.yml by "
                "hand, or simply regenerating.") from e
        if "dirty" in msg:
            raise UpdateNotPossible(
                f"{msg}\n\n"
                "Copier merges template changes against committed state, so "
                "the destination repo must be clean. Commit or stash the "
                "changes in 'applications' and retry.") from e
        raise UpdateNotPossible(msg) from e

    # Board files live outside the template (their set is discovered), so
    # run_update does not touch them. Rewrite them from the stored answers so an
    # updated app picks up board-file improvements too.
    if answers_path.exists():
        stored = yaml.safe_load(answers_path.read_text()) or {}
        answers = build_answers(
            app_name=stored.get("app_name", Path(dest).name),
            description=stored.get("description", ""),
            net_type=stored.get("net_type", "none"),
            ip_mode=stored.get("ip_mode", "dhcp"),
            ipv4_addr=stored.get("ipv4_addr", "192.168.1.15"),
            ipv4_mask=stored.get("ipv4_mask", "255.255.255.0"),
            ipv4_gw=stored.get("ipv4_gw", "192.168.1.1"),
            use_rpc=stored.get("use_rpc", False),
            use_tracing=stored.get("use_tracing", False),
            use_nv=stored.get("use_nv", False),
            use_shell=stored.get("use_shell", False),
            use_led=stored.get("use_led", False),
            modules=stored.get("modules") or {},
            echoserver_transport=stored.get("echoserver_transport", "udp"),
            board_list=sorted(_boards_on_disk(dest)), base=base)
        refreshed = dict(board_files(answers, base))
        refreshed["conf/modules.conf"] = modules_conf(answers, base)
        for rel, content in refreshed.items():
            pth = Path(dest) / rel
            pth.parent.mkdir(parents=True, exist_ok=True)
            pth.write_text(content)
