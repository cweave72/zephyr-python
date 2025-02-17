import logging
import socket
import typing as t

import protorpc.connection.cobs as cobs
from protorpc.connection import setdefault
from protorpc.connection import BaseConnection
from protorpc.connection.cobs import Deframer

logger = logging.getLogger(__name__)

DEFAULT_PORT = 13001


class TcpConnection(BaseConnection):
    """A connection class using TCP + COBS.
    """

    def __init__(self, **kwargs):

        # Set the default port for TCP.
        setdefault(kwargs, 'port', DEFAULT_PORT)
        super().__init__('tcpconn', **kwargs)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.deframer = Deframer()
        self.is_connected = False

    def connect(self, timeout=3, rcvbuf_size=1024):
        self.rcv_timeout = timeout
        self.rcvbuf_size = rcvbuf_size
        self.socket.settimeout(self.rcv_timeout)
        try:
            self.socket.connect((self.addr, self.port))
            self.is_connected = True
            self.start()
            logger.debug(f"TcpConnection connected {self.addr}:{self.port}")
        except Exception as e:
            logger.error(f"Error connecting to {self.addr}:{self.port}")
            raise e

    def encode(self, data: t.ByteString) -> t.ByteString:
        """Encodes data (used for testing).
        """
        return cobs.encode(data)

    def write(self, data: t.ByteString, raw_write=False) -> None:
        """Sends data.
        """
        logger.debug(f"Writing data[{len(data)}]={self.bytes_to_hex(data, 64)} "
                     f"to {self.addr}:{self.port}")
        if self.is_connected:
            if raw_write:
                self.socket.send(data)
            else:
                # COBS encode and add framing.
                encoded = self.encode(data)
                framed = bytearray([0]) + encoded + bytearray([0])
                logger.debug(f"Framed+encoded[{len(framed)}]: "
                             f"{self.bytes_to_hex(framed, 128)}")
                self.socket.send(framed)
        else:
            logger.warning("Tcp write: Not Connected. Call connect() before write().")

    def shutdown(self):
        self.socket.shutdown(socket.SHUT_RDWR)

    def close(self):
        """Closes the connection.
        """
        super().close()
        logger.debug("TcpConnection closing.")
        self.socket.close()

    def read_loop(self):
        """Reads the socket for data.
        """
        try:
            data = self.socket.recv(self.rcvbuf_size)
            if not data:
                logger.debug("recv returned None")
                return None

            msg = self.deframer.process(data)
            if msg:
                logger.debug(f"Received data[{len(data)}]={self.bytes_to_hex(data, 64)}")
                return msg

        except socket.timeout:
            logger.debug("recv timeout")
            return None
        except Exception as e:
            logger.exception(f"Tcp read_loop: {str(e)}")
            self.socket.close()
            return None
