import logging
from protorpc.util import CallsetBase

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

MAX_READ_CHUNK_SIZE = 1000

class SystemRpcError(Exception):
    pass


class SystemRpc(CallsetBase):
    """Class which provides access to the Lfs_PartRpc callset.
    """
    name = "SystemCallset"

    def __init__(self, api):
        super().__init__(api)

    def dump_mem(self, addr: int, size: int) -> bytes:
        """Dumps memory.
        """
        reply = self.api.dumpmem(address=addr, size=size)
        self.check_reply(reply)
        return reply.result.mem

    def get_memory(self, base_addr: int, size: int) -> bytes:
        """Reads memory from device from base_addr.
        Returns bytes.
        """
        num_chunks = size // MAX_READ_CHUNK_SIZE
        leftover = size % MAX_READ_CHUNK_SIZE
        logger.info(f"Reading {num_chunks} chunks of {MAX_READ_CHUNK_SIZE} "
                    f"bytes plus {leftover}.")

        addr = base_addr
        bytes_read = bytearray()
        for k in range(num_chunks):
            logger.info(f"[{k}] reading addr 0x{addr:08x}, {MAX_READ_CHUNK_SIZE} bytes.")
            try:
                read = self.dump_mem(addr, MAX_READ_CHUNK_SIZE)
                bytes_read += read
                addr += MAX_READ_CHUNK_SIZE
            except Exception as e:
                msg = f"[{k}] reading addr 0x{addr:08x}, {MAX_READ_CHUNK_SIZE} bytes."
                logger.error(f"Error: {msg}: {str(e)}")
                raise e

        # Read leftover
        logger.info(f"Reading addr 0x{addr:08x}, {leftover} bytes.")
        try:
            read = self.dump_mem(addr, leftover)
            bytes_read += read
        except Exception as e:
            msg = f"reading addr 0x{addr:08x}, {leftover} bytes."
            logger.error(f"Error: {msg}: {str(e)}")
            raise e

        return bytes_read

    def dump_traceram(self, chunk_size=2000):
        """Dumps current contents of traceram.
        Returns bytearay)
        """
        # First disable trace ram
        logger.info("Diabling trace.")
        try:
            self.disable_trace()
        except Exception as e:
            logger.error(f"{str(e)}")
            return bytearray()

        # Get status
        state, count = self.get_trace_status()
        logger.info(f"Trace ram: state={state}; count={count}")

        # Read until empty.
        ram = bytearray()
        num = 0
        empty_on_read = False
        while not empty_on_read:
            empty_on_read, buf = self.get_next_traceram(chunk_size)
            ram += buf
            num += len(buf)
            logger.info("get_next_traceram: "
                        f"empty={empty_on_read}; len={len(buf)}; total={num}")

        logger.info("Re-enabling trace.")
        self.enable_trace()
        return ram

    def enable_trace(self):
        """Enables tracing.
        """
        reply = self.api.enabletraceram()
        self.check_reply(reply)
        if reply.result.state != 1:
            raise SystemRpcError("Error enabling trace.")

    def disable_trace(self):
        """Disables tracing.
        """
        reply = self.api.disabletraceram()
        self.check_reply(reply)
        if reply.result.state != 0:
            raise SystemRpcError("Error disabling trace.")

    def get_trace_status(self):
        """Gets trace ram status.
        Returns (state, count)
        """
        reply = self.api.gettraceramstatus()
        self.check_reply(reply)
        return reply.result.state, reply.result.count

    def get_next_traceram(self, max_size: int):
        """Gets trace ram buffer.
        Returns (empty_on_read, bytearray).
        """
        reply = self.api.getnexttraceram(max_size)
        self.check_reply(reply)
        return reply.result.empty_on_read, reply.result.data
