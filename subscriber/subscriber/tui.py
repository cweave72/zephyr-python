import logging
from logging import LogRecord

from rich.logging import RichHandler
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.binding import Binding
from textual.widgets import (
    Header,
    Footer,
    Input,
    Button,
    Checkbox,
    Select,
    Label,
    RichLog,
)

from subscriber import client, sysmsg

logger = logging.getLogger(__name__)


class Body(Container):
    pass


class LogViewer(RichLog):
    """Dedicated pane for app-level diagnostic log records. Not used for
    MQTT topic traffic -- see TopicViewport/WidgetConsole for that."""

    def __init__(self, logger, *args, **kwargs):
        super().__init__(*args, **kwargs)

        logger.setLevel(logging.DEBUG)
        handler = self.LogHandler(self, show_time=False)
        logger.addHandler(handler)

    class LogHandler(RichHandler):

        def __init__(self, textlog: RichLog, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.textlog = textlog

        def emit(self, record: LogRecord):
            message = self.format(record)
            msg_renderable = self.render_message(record, message)
            log_renderable = self.render(record=record, traceback=None,
                                         message_renderable=msg_renderable)
            self.textlog.write(log_renderable)


class WidgetConsole:
    """Stand-in for rich.console.Console, passed as SubscriberClient's
    console= kwarg. Forwards .print() calls into a RichLog widget owned by
    one TopicViewport, hopping onto the Textual app's thread since MQTT
    callbacks fire on paho's loop_start() background thread. Pausing
    suppresses the display write only -- decoding/logfile side effects in
    handle() are unaffected."""

    def __init__(self, app, richlog):
        self._app = app
        self._richlog = richlog
        self.paused = False

    def print(self, renderable, *args, **kwargs):
        if self.paused:
            return
        self._app.call_from_thread(self._richlog.write, renderable)


class TopicViewport(Container):
    """One subscription's display: a title label, a RichLog body, and
    Pause/Stop controls. Used both for ad-hoc subscriptions created via the
    ControlPanel and for the always-on sysmsg subscription."""

    DEFAULT_CSS = """
        TopicViewport {
            height: auto;
            border: round $accent;
            margin: 0;
        }

        TopicViewport > Horizontal {
            height: 1;
        }

        TopicViewport > Horizontal > Label {
            width: 1fr;
        }

        TopicViewport > RichLog {
            height: 12;
        }
    """

    def __init__(self, topic, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.topic = topic
        self.richlog = RichLog(highlight=False, markup=False, wrap=True)
        self.console = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Label(self.topic)
            yield Button("Pause", classes="pause-btn", compact=True)
            yield Button("Stop", classes="stop-btn", compact=True)
        yield self.richlog


class ControlPanel(Container):
    """Form for starting a new ad-hoc subscription from within the TUI."""

    DEFAULT_CSS = """
        ControlPanel {
            height: auto;
            border: round $primary;
            padding: 0 1;
        }

        ControlPanel > Horizontal {
            height: auto;
        }

        ControlPanel > Horizontal > Input {
            width: 1fr;
        }

        ControlPanel > Horizontal > Checkbox,
        ControlPanel > Horizontal > Button {
            width: auto;
        }
    """

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Input(placeholder="topic (e.g. sysmsg)", id="topic-input")
            yield Input(placeholder="pub id (optional)", id="pub-input")
            yield Checkbox("All", id="all-checkbox")
            yield Button("Subscribe", id="subscribe-btn", variant="primary")


class TuiApp(App):
    TITLE = "Tui"

    DEFAULT_CSS = """
        Body {
            height: 4fr;
        }

        #topics {
            height: 1fr;
        }

        #sysmsg-slot {
            height: auto;
        }

        LogViewer {
            height: 1fr;
            display: none;
        }

        #sysmsg-level-bar {
            height: auto;
            dock: bottom;
        }
    """

    BINDINGS = [
        Binding("ctrl+c,ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+l", "toggle_log", "Toggle Log", show=True),
    ]

    def __init__(self, logger, host, port, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logger
        self.host = host
        self.port = port
        self.subscriptions = {}
        self.sysmsg_subscriber = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Body(
            ControlPanel(),
            VerticalScroll(id="topics"),
            Container(id="sysmsg-slot"),
            LogViewer(self.logger, id="log"),
        )
        with Horizontal(id="sysmsg-level-bar"):
            yield Label("Sysmsg level:")
            yield Select([(lvl, lvl) for lvl in sysmsg.LEVEL_CHOICES], value="debug",
                         id="sysmsg-level-select", allow_blank=False)
        yield Footer()

    async def on_mount(self) -> None:
        topic = client.resolve_topic("sysmsg", True, None)
        viewport = TopicViewport(topic)
        console = WidgetConsole(self, viewport.richlog)
        viewport.console = console

        subscriber = sysmsg.MsgSubscriberClient(
            self.host, self.port, topic, level="debug", console=console)
        try:
            subscriber.start()
        except OSError as e:
            self.logger.error("failed to connect to %s:%s: %s", self.host, self.port, e)

        self.subscriptions[viewport] = subscriber
        self.sysmsg_subscriber = subscriber
        await self.query_one("#sysmsg-slot", Container).mount(viewport)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("topic-input", "pub-input"):
            self._subscribe()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "subscribe-btn":
            self._subscribe()
        elif "pause-btn" in event.button.classes:
            node = self._find_viewport(event.button)
            if node is not None:
                node.console.paused = not node.console.paused
                event.button.label = "Resume" if node.console.paused else "Pause"
        elif "stop-btn" in event.button.classes:
            node = self._find_viewport(event.button)
            if node is not None:
                self._stop(node)

    def action_toggle_log(self) -> None:
        log = self.query_one("#log", LogViewer)
        log.display = not log.display

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "sysmsg-level-select" and self.sysmsg_subscriber is not None:
            self.sysmsg_subscriber.threshold = sysmsg.SEVERITY[event.value.upper()]

    def _find_viewport(self, node):
        while node is not None and not isinstance(node, TopicViewport):
            node = node.parent
        return node

    def _subscribe(self) -> None:
        topic = self.query_one("#topic-input", Input).value.strip()
        if not topic:
            return
        pub_raw = self.query_one("#pub-input", Input).value.strip()
        all_ = self.query_one("#all-checkbox", Checkbox).value

        pub = None
        if pub_raw:
            try:
                pub = int(pub_raw)
            except ValueError:
                self.logger.error("pub id must be an integer: %r", pub_raw)
                return

        try:
            resolved_topic = client.resolve_topic(topic, all_, pub)
        except ValueError as e:
            self.logger.error(str(e))
            return

        viewport = TopicViewport(resolved_topic)
        console = WidgetConsole(self, viewport.richlog)
        viewport.console = console

        subscriber = client.SubscriberClient(
            self.host, self.port, resolved_topic, console=console)

        try:
            subscriber.start()
        except OSError as e:
            self.logger.error("failed to connect to %s:%s: %s", self.host, self.port, e)
            return

        self.subscriptions[viewport] = subscriber
        self.query_one("#topics", VerticalScroll).mount(viewport)

    def _stop(self, viewport: TopicViewport) -> None:
        subscriber = self.subscriptions.pop(viewport, None)
        if subscriber is not None:
            subscriber.stop()
        viewport.remove()

    def action_quit(self):
        for subscriber in self.subscriptions.values():
            subscriber.stop()
        self.subscriptions.clear()
        self.exit()

    def on_ready(self):
        pass
