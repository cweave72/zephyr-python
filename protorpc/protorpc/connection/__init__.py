import time
import datetime
import logging
import socket
import typing as t
from threading import Thread, Event
from queue import Queue

logger = logging.getLogger(__name__)


def setdefault(d: t.Dict, key: t.Any, default: t.Any):
    """Writes default for dict[key] if key is missing or None.
    """
    d.setdefault(key, None)
    if d[key] is None:
        d[key] = default


class BaseConnection(Thread):
    """Base connection class.
    """

    def __init__(self, name, *args, **kwargs):

        # Extract connection kwargs.
        self.addr = kwargs.pop('addr', None)
        self.hostname = kwargs.pop('hostname', None)
        self.port = kwargs.pop('port', None)
        self.timeout = kwargs.pop('timeout', 2)

        if all(item is None for item in [self.addr, self.hostname]):
            raise Exception("Either 'addr' or 'hostname' must be provided.")

        # If IP addr is given, this takes precedence.
        if self.addr is None and self.hostname is not None:
            try:
                self.addr = socket.gethostbyname(self.hostname)
                logger.debug(f"Resolved addr={self.addr} from hostname={self.hostname}")
            except Exception as e:
                logger.error(f"Error resolving IP from {self.hostname}.")
                raise e

        super().__init__(*args, **kwargs)
        self.name = name
        self.seqn = 0

        self.pending_request = None
        self.event = Event()
        self.daemon = True

    def get_next_seqn(self):
        """Iterates and returns the sequence number.
        """
        self.seqn += 1
        return self.seqn

    def shutdown(self):
        pass

    def stop(self):
        """Stops the connection service.
        """
        self.event.set()

    def close(self):
        """Close the connection.
        """
        self.stop()
        self.join()

    def bytes_to_hex(self, data: bytes, clamp=None) -> str:
        """Converts a bytes stream to hex chars.
        """
        clamp = len(data) if clamp is None else clamp
        hex_str = [f"0x{h:02x}," for h in data[:clamp]]
        if clamp < len(data):
            return ''.join(hex_str) + '...'
        return ''.join(hex_str)

    def add_pending(self, request):
        """Adds a request to the pending list.
        """
        self.pending_request = request

    #def remove_pending(self, seqn):
    #    """Removes a request to the pending list.
    #    """
    #    for r_seqn in self.pending_requests:
    #        if r_seqn == seqn:
    #            logger.debug(f"Removing seqn={seqn} from pending list.")
    #            self.pending_requests.pop(seqn)
    def remove_pending(self, seqn):
        self.pending_request = None

    def read_loop(self):
        """Read from port.  Must be implemented by subclass.
        """
        raise NotImplementedError

    def run(self):

        logger.debug("Starting thread loop.")

        while True:

            if self.event.is_set():
                logger.debug("Base thread stopping.")
                break

            if self.pending_request is not None:
                data = self.read_loop()

                if data is not None:
                    reply = self.pending_request.reply
                    try:
                        pos = reply.rcv_header(data)
                        if reply.seqn == self.pending_request.seqn:
                            reply.rcv_handler(data, pos)
                            logger.debug(f"Got reply for seqn={reply.seqn}")
                            self.pending_request.got_reply = True
                            self.remove_pending(reply.seqn)
                            continue
                        else:
                            logger.warning(f"Received seqn ({reply.seqn}) "
                                           f"does not match pending seqn "
                                           f"({self.pending_request.seqn})")
                    except Exception as e:
                        logger.exception("Error receiving data, "
                                         "dropping request with "
                                         f"seqn={self.pending_request.seqn}: {str(e)}.")
                        self.remove_pending(0)
                        continue

                # Test for pending request timeout.
                if datetime.datetime.now() > self.pending_request.ttl:
                    logger.error("Removing request frame due to timeout: "
                                 f"{self.pending_request.callset}")
                    self.pending_request.timedout = True
                    self.remove_pending(0)

            time.sleep(0.1)
