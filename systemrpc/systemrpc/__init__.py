import logging
from protorpc.util import CallsetBase

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

MAX_READ_CHUNK_SIZE = 1000

class SystemRpc(CallsetBase):
    """Class which provides access to the Lfs_PartRpc callset.
    """
    name = "SystemCallset"

    def __init__(self, api):
        super().__init__(api)

    def get_trace_addr(self):
        """Gets Tracing memory information.
        Returns tuple (addr, size)
        """
        reply = self.api.gettraceramaddr()
        self.check_reply(reply)
        return reply.result.address, reply.result.size

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
