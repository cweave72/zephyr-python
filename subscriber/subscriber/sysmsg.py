from datetime import datetime

from rich.text import Text

from subscriber.client import SubscriberClient

LEVELS = {
    0x30: ("CRITICAL", 50, "bold white on red"),
    0x31: ("ERROR", 40, "bold red"),
    0x32: ("WARN", 30, "yellow"),
    0x33: ("INFO", 20, "cyan"),
    0x34: ("DEBUG", 10, "dim"),
}

SEVERITY = {name: rank for name, rank, _ in LEVELS.values()}

LEVEL_CHOICES = ("debug", "info", "warn", "error", "critical")

BOLD_TEXT_LEVELS = {"CRITICAL", "ERROR", "WARN"}


class MsgSubscriberClient(SubscriberClient):
    """Subscriber for the Publisher module's PUB_TYPE_MSG ("sysmsg") topic.

    Wire format is a 1-byte ASCII digit level prefix followed by plain text
    (see Publisher.h). Every message received is logged to file regardless
    of level; only messages at or above the --level threshold are also
    printed to the console.
    """

    def __init__(self, host, port, topic="sysmsg", level="debug",
                 logfile="sysmsg.log", console=None):
        super().__init__(host, port, topic, console=console)
        self.threshold = SEVERITY[level.upper()]
        self.logfile = logfile

    def decode(self, payload):
        level_name, rank, style = LEVELS.get(payload[0], ("UNKNOWN", 0, "white"))
        text = payload[1:].decode("utf-8", errors="replace")
        return level_name, rank, style, text

    def handle(self, decoded, pub_id):
        level_name, rank, style, text = decoded
        timestamp = datetime.now()
        pub_str = f"pub:{pub_id}" if pub_id is not None else "pub:???"

        with open(self.logfile, "a") as f:
            f.write(f"{timestamp.isoformat()} {pub_str} [{level_name:>8}] {text}\n")

        if rank >= self.threshold:
            line = Text()
            line.append(f"{timestamp:%H:%M:%S} ", style="dim")
            line.append(f"{pub_str} ", style="bold cyan")
            line.append(f"[{level_name:>8}] ", style=style)
            text_style = "bold" if level_name in BOLD_TEXT_LEVELS else None
            line.append(text, style=text_style)
            self.console.print(line)
