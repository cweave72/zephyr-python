import sys
import logging

from pathlib import Path
from rich.table import Table
from rich.pretty import Pretty
from rich import box

from trace_tool import bytesToHexStr
from trace_tool.ctf_config import EventFrame


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class TraceParserError(Exception):
    pass


class TraceParser:
    def __init__(self, tracefile: str):
        if not Path(tracefile).exists():
            raise TraceParserError(f"Trace file {tracefile} does not exist.")

        self.f = Path(tracefile)
        self.items = []

    def parse_events(self, max_items=None):
        """Parses a bytestream to an array of Events."""

        data = self.f.read_bytes()
        self.items = []
        cursor = 0

        while True:
            if cursor >= len(data):
                break

            ev = EventFrame(data[cursor:])
            if not ev.success:
                raise TraceParserError(
                    f"EventFrame error at cursor={cursor} "
                    f"(data={bytesToHexStr(data[cursor : cursor + 16])}..."
                )

            cursor += ev.event_frame_size
            self.items.append(ev)

            if max_items is not None and len(self.items) == max_items:
                break

        logger.info(f"Processed {len(self.items)} trace items.")

    def calc_delta(self, tstamp: int, tstamp_d1: int):
        """Computes timestamp delta accounting for wrap."""
        if tstamp >= tstamp_d1:
            return tstamp - tstamp_d1
        else:
            return (2**32 - tstamp_d1) + tstamp

    def get_thread_name(self, event):
        if "thread_name" in event.fields:
            if len(event.thread_name) == 0:
                return f"0x{event.thread_id:08x}"
            else:
                return event.thread_name
        else:
            return "--"

    def build_table(self, max_items=None):
        if not self.items:
            try:
                self.parse_events(max_items=max_items)
            except Exception as e:
                logger.exception(f"{str(e)}")
                return

        table = Table(title="Trace", box=box.ROUNDED)

        table.add_column("Time", style="magenta")
        table.add_column("Elapsed", style="yellow")
        table.add_column("Run", style="yellow")
        table.add_column("Thread Name", style="cyan")
        table.add_column("Event", style="blue")
        table.add_column("Parameters", style="orange3")

        elapsed = 0
        stamp_next = self.items[1].timestamp

        for k, item in enumerate(self.items):
            event = item.event

            run = self.calc_delta(stamp_next, item.timestamp)
            thread_name = ""

            thread_name = self.get_thread_name(event)

            fields = event.fields
            field_params = []
            for field in fields:
                field_params.append(f"{field}={getattr(event, field)}")

            rows = []
            rows.append(f"0x{item.timestamp:08x}")
            rows.append(Pretty(elapsed))
            rows.append(f"{run}")
            rows.append(thread_name)
            rows.append(str(event))
            rows.append(", ".join(field_params))

            try:
                stamp_next = self.items[k + 2].timestamp
            except Exception:
                pass

            elapsed += run
            table.add_row(*rows)

        return table


if __name__ == "__main__":
    from rich.logging import RichHandler
    from pathlib import Path

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    ch = RichHandler(rich_tracebacks=True, show_time=False)
    ch.setLevel(logging.DEBUG)
    root.addHandler(ch)

    logger.info(sys.argv)

    p = Path(sys.argv[1])
    data = p.read_bytes()

    parse_events(data)
