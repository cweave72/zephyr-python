"""Textual TUI for the Zephyr application generator.

Three scrollable columns: system-level choices, optional modules, and a live
manifest. Structure follows python/subscriber/tui.py -- per-widget DEFAULT_CSS
rather than an external .tcss, and app.call_from_thread to get worker output
onto the UI thread.

The manifest is rendered from generate.plan_files(), the same function behind
`app_gen new --dry-run`, so the preview cannot drift from what is written.
"""
import logging
import re

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.binding import Binding
from textual.widgets import (
    Header, Footer, Input, Button, Checkbox, Select, Label, Static,
    RadioSet, RadioButton, SelectionList, RichLog,
)
from textual.widgets.selection_list import Selection

from app_generator import boards as boards_mod
from app_generator import generate as gen
from app_generator import modules as modules_mod

logger = logging.getLogger(__name__)

NET_TYPES = ["none", "wifi", "serial", "eth", "usb"]
DOTTED_QUAD = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def valid_ipv4(text):
    m = DOTTED_QUAD.match((text or "").strip())
    return bool(m) and all(0 <= int(g) <= 255 for g in m.groups())


class ModuleRow(Horizontal):
    """One module: a checkbox plus its log-level Select.

    A plain SelectionList cannot host a per-row widget, which is why the module
    column is built from rows rather than being a SelectionList like boards.
    """

    DEFAULT_CSS = """
    ModuleRow { height: 3; }
    ModuleRow > Checkbox { width: 1fr; }
    ModuleRow > Select { width: 14; }
    """

    def __init__(self, name):
        super().__init__()
        self.mod_name = name

    def compose(self) -> ComposeResult:
        yield Checkbox(self.mod_name, id=f"mod_{self.mod_name}")
        yield Select(
            [(lvl, lvl) for lvl in modules_mod.LOG_LEVELS],
            value=modules_mod.DEFAULT_LOG_LEVEL,
            allow_blank=False,
            disabled=True,
            id=f"lvl_{self.mod_name}",
        )


class TuiApp(App):
    """Zephyr application generator."""

    TITLE = "Zephyr App Generator"

    CSS = """
    #cols { height: 1fr; }
    #sys, #mods, #man { width: 1fr; border: round $accent; padding: 0 1; }
    #man { width: 1.4fr; }
    .heading { text-style: bold; color: $accent; }
    .warn { color: $warning; }
    #generate { width: 100%; margin: 1 0; }
    Input.-invalid { border: tall $error; }
    """

    BINDINGS = [
        Binding("ctrl+g", "generate", "Generate"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="cols"):
            with VerticalScroll(id="sys"):
                yield Label("System", classes="heading")
                yield Label("Name")
                yield Input(placeholder="my_app", id="app_name")
                yield Label("Description")
                yield Input(value="A Zephyr application.", id="description")

                yield Label("Network")
                yield Select([(n, n) for n in NET_TYPES], value="wifi",
                             allow_blank=False, id="net_type")

                yield Label("Addressing", id="ip_label")
                with RadioSet(id="ip_mode"):
                    yield RadioButton("DHCP", value=True, id="ip_dhcp")
                    yield RadioButton("Static", id="ip_static")
                yield Input(value="192.168.1.15", id="ipv4_addr", disabled=True)
                yield Input(value="255.255.255.0", id="ipv4_mask", disabled=True)
                yield Input(value="192.168.1.1", id="ipv4_gw", disabled=True)

                yield Checkbox("RPC server", value=True, id="use_rpc")
                yield Checkbox("Tracing", id="use_tracing")
                yield Checkbox("NV settings", value=True, id="use_nv")
                yield Checkbox("Shell", id="use_shell")
                yield Checkbox("Onboard LED", value=True, id="use_led")

                yield Label("Boards", classes="heading")
                yield SelectionList(
                    *[Selection(f"{n}  ({h or '-'})", n)
                      for n, _t, h in boards_mod.all_boards()],
                    id="boards")
                yield Button("Generate", variant="primary", id="generate",
                             disabled=True)

            with VerticalScroll(id="mods"):
                yield Label("Modules", classes="heading")
                for name in modules_mod.SELECTABLE:
                    yield ModuleRow(name)
                yield Label("Pulled in by deps:", classes="heading")
                yield Static("-", id="closure")

            with VerticalScroll(id="man"):
                yield Label("Manifest", classes="heading")
                yield Static("", id="manifest")
                yield Static("", id="warnings", classes="warn")
                yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self):
        self.refresh_manifest()

    # ---- answer assembly -------------------------------------------------

    def selected_modules(self):
        out = {}
        for name in modules_mod.SELECTABLE:
            cb = self.query_one(f"#mod_{name}", Checkbox)
            if cb.value:
                sel = self.query_one(f"#lvl_{name}", Select)
                out[name] = sel.value or modules_mod.DEFAULT_LOG_LEVEL
        return out

    def current_answers(self):
        net = self.query_one("#net_type", Select).value
        static = self.query_one("#ip_static", RadioButton).value
        return gen.build_answers(
            app_name=self.query_one("#app_name", Input).value.strip() or "app",
            description=self.query_one("#description", Input).value.strip(),
            net_type=net,
            ip_mode="static" if static else "dhcp",
            ipv4_addr=self.query_one("#ipv4_addr", Input).value.strip(),
            ipv4_mask=self.query_one("#ipv4_mask", Input).value.strip(),
            ipv4_gw=self.query_one("#ipv4_gw", Input).value.strip(),
            use_rpc=self.query_one("#use_rpc", Checkbox).value,
            use_tracing=self.query_one("#use_tracing", Checkbox).value,
            use_nv=self.query_one("#use_nv", Checkbox).value,
            use_shell=self.query_one("#use_shell", Checkbox).value,
            use_led=self.query_one("#use_led", Checkbox).value,
            modules=self.selected_modules(),
            board_list=list(self.query_one("#boards", SelectionList).selected),
        )

    # ---- reactive wiring -------------------------------------------------

    def on_select_changed(self, event: Select.Changed):
        self.apply_rules()
        self.refresh_manifest()

    def on_checkbox_changed(self, event: Checkbox.Changed):
        self.apply_rules()
        self.refresh_manifest()

    def on_input_changed(self, event: Input.Changed):
        self.refresh_manifest()

    def on_radio_set_changed(self, event: RadioSet.Changed):
        self.apply_rules()
        self.refresh_manifest()

    def on_selection_list_selected_changed(self, event):
        self.refresh_manifest()

    def apply_rules(self):
        """Enforce the interactions between controls."""
        net = self.query_one("#net_type", Select).value
        networked = net != "none"

        # RPC needs a network.
        rpc = self.query_one("#use_rpc", Checkbox)
        rpc.disabled = not networked
        if not networked and rpc.value:
            rpc.value = False

        # Addressing only applies when there is a network.
        static = self.query_one("#ip_static", RadioButton).value
        self.query_one("#ip_mode", RadioSet).disabled = not networked
        for wid in ("ipv4_addr", "ipv4_mask", "ipv4_gw"):
            self.query_one(f"#{wid}", Input).disabled = not (networked and static)

        # Per-module log level is meaningless until the module is ticked.
        for name in modules_mod.SELECTABLE:
            ticked = self.query_one(f"#mod_{name}", Checkbox).value
            self.query_one(f"#lvl_{name}", Select).disabled = not ticked

        # TRACEMODULE depends on TRACERAM, which the tracing option enables.
        tracing = self.query_one("#use_tracing", Checkbox)
        if self.query_one("#mod_TraceModule", Checkbox).value:
            tracing.value = True
            tracing.disabled = True
        else:
            tracing.disabled = False

    def form_errors(self):
        errs = []
        name = self.query_one("#app_name", Input).value.strip()
        if not name:
            errs.append("Name is required.")
        elif not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            errs.append("Name must be lowercase alphanumeric/underscore.")
        if not self.query_one("#boards", SelectionList).selected:
            errs.append("Select at least one board.")

        net = self.query_one("#net_type", Select).value
        if net != "none" and self.query_one("#ip_static", RadioButton).value:
            for wid, lbl in (("ipv4_addr", "address"), ("ipv4_mask", "netmask"),
                             ("ipv4_gw", "gateway")):
                field = self.query_one(f"#{wid}", Input)
                ok = valid_ipv4(field.value)
                field.set_class(not ok, "-invalid")
                if not ok:
                    errs.append(f"Static {lbl} is not a valid IPv4 address.")
        return errs

    def refresh_manifest(self):
        try:
            answers = self.current_answers()
        except Exception as e:                    # keep the UI alive
            self.query_one("#manifest", Static).update(f"[red]{e}[/red]")
            return

        name = self.query_one("#app_name", Input).value.strip() or "<name>"
        files = gen.plan_files(answers)
        body = [f"[bold]applications/{name}/[/bold]"]
        body += [f"  {f}" for f in files]
        self.query_one("#manifest", Static).update("\n".join(body))

        added = answers["module_symbols"]["added"]
        self.query_one("#closure", Static).update(
            "\n".join(f"  {s}" for s in added) if added else "-")

        warns = list(self.form_errors())
        net = answers["net_type"]
        if net != "none":
            for b in answers["board_list"]:
                hint = boards_mod.net_hint(b)
                if hint and hint != net:
                    warns.append(f"{b} normally uses '{hint}', app is '{net}'.")
        if net == "serial":
            warns.append("serial: you must set zephyr,uart-pipe in the board "
                         "overlay before it will build.")
        self.query_one("#warnings", Static).update(
            "\n".join(f"! {w}" for w in warns))
        self.query_one("#generate", Button).disabled = bool(self.form_errors())

    # ---- generate --------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "generate":
            self.action_generate()

    def action_generate(self):
        if self.form_errors():
            return
        answers = self.current_answers()
        dest = gen.default_dest(answers["app_name"])
        log = self.query_one("#log", RichLog)
        log.write(f"[bold]Generating[/bold] {dest} ...")
        self.run_worker(
            lambda: self._generate_worker(answers, dest),
            thread=True, exclusive=True)

    def _generate_worker(self, answers, dest):
        log = self.query_one("#log", RichLog)
        try:
            written = gen.run(answers, dest)
        except Exception as e:
            self.call_from_thread(log.write, f"[red]Failed:[/red] {e}")
            return
        self.call_from_thread(
            log.write, f"[green]Wrote {len(written)} files to[/green] {dest}")
        self.call_from_thread(
            log.write,
            "[yellow]Note:[/yellow] 'applications' is west-managed -- be on "
            "the 'main' branch before committing.")
