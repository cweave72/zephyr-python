import queue
import typing as t
import logging

from queue import Queue
from rich.logging import RichHandler

logger = logging.getLogger(__name__)

ESCAPED_BYTE = 0x00


def encode(bytes_in: t.ByteString) -> t.ByteString:
    """Perform COBS encoding on the input byte stream.
    """
    max_overhead = round(len(bytes_in)/254 + 0.5)
    enc_out = [0] * (max_overhead + len(bytes_in))
    count = 0
    code_word_idx = 0

    for byte in bytes_in:
        count += 1

        # If escaped byte or 255 bytes without an escaped byte.
        if byte == ESCAPED_BYTE or count == 255:
            # Write the code word and advance codeword index..
            enc_out[code_word_idx] = count
            code_word_idx += count

            if count == 255:
                wr_idx = code_word_idx + 1
                enc_out[wr_idx] = byte
                count = 1
            else:
                count = 0
        else:
            wr_idx = code_word_idx + count
            enc_out[wr_idx] = byte

        #logger.debug(f"count={count}; code_word_idx={code_word_idx}")

    enc_out[code_word_idx] = count + 1
    length = code_word_idx + count + 1

    # Trim output for return
    enc_out = enc_out[:length]
    return bytearray(enc_out)


def decode(encbytes_in: t.ByteString) -> t.ByteString:
    """Perform COBS decoding on the input byte stream.
    """
    dec_out = [0] * len(encbytes_in)
    code_idx = 0
    data_idx = 1
    out_idx = 0
    num = 0

    while True:
        code = encbytes_in[code_idx]
        #logger.debug(f"out_idx={out_idx}; data_idx={data_idx}; num={num}; "
        #             f"code_idx={code_idx}; code={code}")

        count = 1
        while count < code:
            dec_out[out_idx] = encbytes_in[data_idx]
            out_idx += 1
            data_idx += 1
            num += 1
            count += 1

        code_idx += count
        data_idx = code_idx + 1

        if code_idx == len(encbytes_in):
            break

        if count == 255:
            continue

        # Insert escaped zero.
        dec_out[out_idx] = 0
        out_idx += 1
        num += 1

    return bytearray(dec_out[:num])


class Deframer:
    def __init__(self):
        self.state = "INIT"
        self.q = Queue()
        self.data = []
        self.count = 0

    def process(self, new_data):
        """Processes new data, returns decoded message if framing detected.
        """
        # Push new data into the queue.
        for b in new_data:
            self.q.put_nowait(b)

        while True:

            if self.state == "INIT":
                logger.debug("DEFRAMER: INIT")
                self.data = []
                self.count = 0
                self.state = "FIND_SOF"

            elif self.state == "FIND_SOF":
                while True:
                    try:
                        byte = self.q.get_nowait()
                    except queue.Empty:
                        logger.debug("DEFRAMER: FIND_SOF: fifo empty")
                        self.state = "INIT"
                        return None
                    except Exception as e:
                        logger.error(f"{str(e)}")
                        self.state = "INIT"
                        return None

                    if byte == 0:
                        logger.debug("DEFRAMER: FIND_SOF: Found framing.")
                        self.state = "FIND_EOF"
                        break

            elif self.state == "FIND_EOF":
                if self.q.empty():
                    self.state = "INIT"
                    return None

                while True:
                    try:
                        byte = self.q.get_nowait()
                    except queue.Empty:
                        logger.debug("DEFRAMER: FIND_EOF: fifo empty.")
                        self.state = "INIT"
                        return None
                    except Exception as e:
                        logger.error(f"{str(e)}")
                        self.state = "INIT"
                        return None

                    if byte == 0:
                        logger.debug(f"DEFRAMER: FIND_EOF: Found framing. count={self.count}")
                        self.state = "DECODE"
                        break
                    else:
                        self.data.append(byte)
                        self.count += 1

            elif self.state == "DECODE":
                logger.debug("DEFRAMER: DECODE")
                self.state = "INIT"
                return decode(bytearray(self.data))


if __name__ == "__main__":

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    ch = RichHandler(rich_tracebacks=True, show_time=False)
    ch.setLevel(logging.DEBUG)
    logger.addHandler(ch)

    msg = [0]
    enc = encode(bytearray(msg))
    dec = decode(enc)
    if list(dec) != msg:
        logger.error("Fail:")
        logger.error(f"msg={msg}")
        logger.error(f"enc={list(enc)}")
        logger.error(f"dec={list(dec)}")
    else:
        logger.info(f"Pass: msg={msg}")

    msg = [1]
    enc = encode(bytearray(msg))
    dec = decode(enc)
    if list(dec) != msg:
        logger.error("Fail")
        logger.error(f"msg={msg}")
        logger.error(f"enc={list(enc)}")
        logger.error(f"dec={list(dec)}")
    else:
        logger.info(f"Pass: msg={msg}")

    msg = [0, 0]
    enc = encode(bytearray(msg))
    dec = decode(enc)
    if list(dec) != msg:
        logger.error("Fail")
        logger.error(f"msg={msg}")
        logger.error(f"enc={list(enc)}")
        logger.error(f"dec={list(dec)}")
    else:
        logger.info(f"Pass: msg={msg}")

    msg = [0, 1]
    enc = encode(bytearray(msg))
    dec = decode(enc)
    if list(dec) != msg:
        logger.error("Fail")
        logger.error(f"msg={msg}")
        logger.error(f"enc={list(enc)}")
        logger.error(f"dec={list(dec)}")
    else:
        logger.info(f"Pass: msg={msg}")

    msg = list(range(1, 255)) + [255]
    enc = encode(bytearray(msg))
    dec = decode(enc)
    if list(dec) != msg:
        logger.error("Fail")
        logger.error(f"msg={msg}")
        logger.error(f"enc={list(enc)}")
        logger.error(f"dec={list(dec)}")
    else:
        logger.info(f"Pass: msg={msg[:16]}")

    msg = [x % 256 for x in range(1024)]
    enc = encode(bytearray(msg))
    dec = decode(enc)
    if list(dec) != msg:
        logger.error("Fail")
        logger.error(f"msg={msg}")
        logger.error(f"enc={list(enc)}")
        logger.error(f"dec={list(dec)}")
    else:
        logger.info(f"Pass: msg={msg[:16]}")
