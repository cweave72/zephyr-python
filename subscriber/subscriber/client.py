import re
import logging
from datetime import datetime

from paho.mqtt import client as mqtt_client
from rich.console import Console
from rich.text import Text

_PUBID_RE = re.compile(r"/pubid-(\d+)$")

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def parse_pubid(topic):
    """Extracts the publisher id (last IP octet) from a concrete topic like
    "sysmsg/pubid-208", per Publisher_createTopic's "<topic>/pubid-<id>"
    convention. Returns None if the topic doesn't carry a pubid segment."""
    match = _PUBID_RE.search(topic)
    return match.group(1) if match else None


def strip_pubid(topic):
    """Removes the trailing "/pubid-<id>" segment from a concrete topic,
    per Publisher_createTopic's "<topic>/pubid-<id>" convention. Topics
    without a pubid segment are returned unchanged."""
    return _PUBID_RE.sub("", topic)


def resolve_topic(base_topic, all_, pub):
    """Expands a base topic into the concrete wildcard/pubid-scoped topic
    string per Publisher_createTopic's "<topic>/pubid-<id>" convention.
    "#" is passed through as-is: the MQTT match-everything wildcard already
    covers every publisher, so it isn't scoped further."""
    if all_ and pub is not None:
        raise ValueError("--all and --pub are mutually exclusive.")

    if base_topic == "#":
        if pub is not None:
            raise ValueError("--pub is not valid with the '#' wildcard topic.")
        return "#"

    if pub is not None:
        return f"{base_topic}/pubid-{pub}"
    return f"{base_topic}/+"


class SubscriberClient:
    """Wraps a paho MQTT client subscribed to a single topic.

    Subclasses override decode()/handle() for type-specific wire formats;
    the base behavior is a best-effort raw text display, used for any topic
    that has no dedicated subclass.
    """

    def __init__(self, host, port, topic, qos=0, console=None):
        self.host = host
        self.port = port
        self.topic = topic
        self.qos = qos
        self.console = console or Console()

        logger.info(f"Subscribing to {topic=}; {host=}; {port=}")
        self.client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        logger.info("on_connect: subscribing.")
        client.subscribe(self.topic, qos=self.qos)

    def _on_message(self, client, userdata, msg):
        logger.debug(f"{msg.topic=} {msg.payload=}")
        self.handle(self.decode(msg.payload), parse_pubid(msg.topic), msg.topic)

    def decode(self, payload):
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            return repr(payload)

    def handle(self, decoded, pub_id, topic):
        pub_str = f"pub:{pub_id}" if pub_id is not None else "pub:???"
        line = Text()
        line.append(f"{datetime.now():%H:%M:%S} ", style="dim")
        line.append(f"{pub_str} ", style="bold cyan")
        line.append(f"[{strip_pubid(topic)}] ", style="magenta")
        line.append(str(decoded))
        self.console.print(line)

    def run(self):
        self.client.connect(self.host, self.port)
        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            self.client.disconnect()

    def start(self):
        """Non-blocking counterpart to run(): connects and starts paho's own
        background network thread, returning immediately."""
        self.client.connect(self.host, self.port)
        self.client.loop_start()

    def stop(self):
        """Counterpart to start(): disconnects and joins the background
        network thread."""
        self.client.disconnect()
        self.client.loop_stop()
