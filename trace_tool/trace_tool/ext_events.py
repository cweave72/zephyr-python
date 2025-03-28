import struct
import logging
from rich import inspect

from trace_tool import bytesToHexStr
from trace_tool.ctf_config import EventBase

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

# little-endian
le = "<"
int8 = "b"
uint8 = "B"
uint32 = "I"
int32 = "i"
uint16 = "H"
int16 = "h"
ctf_str20 = "20s"
ctf_str46 = "46s"

tracemodule_hdr_fmt = f"{le}{uint8}{uint8}"
tracemodule_hdr_fmt_size = struct.calcsize(tracemodule_hdr_fmt)

class protorpc_header(EventBase):
    fmt = f"{uint32}{uint32}{uint32}"
    fields = (
        "seqn",
        "which_callset",
        "which_msg",
    )
    def __init__(self, data: bytes):
        super().__init__(data)
        self.size += tracemodule_hdr_fmt_size


class ProtoRpcModule:
    events = {
        0x01: protorpc_header,
    }

    def __str__(self):
        return self.__class__.__name__


class TraceModuleError(Exception):
    pass


class TraceModule:
    fmt = tracemodule_hdr_fmt
    fields = (
        "module_id",
        "event_id"
    )

    modules = {
        0x01: ProtoRpcModule,
    }

    def __new__(cls, data: bytes):
        hdrsize = struct.calcsize(cls.fmt)
        unpacked = struct.unpack(cls.fmt, data[:hdrsize])
        module_cls = cls.modules.get(unpacked[0])
        if module_cls is None:
            raise TraceModuleError(f"Unknown module id: {unpacked[0]}")
        event_cls = module_cls.events.get(unpacked[1])
        if event_cls is None:
            raise TraceModuleError(f"Module: {str(module_cls())}, "
                                   f"unknown event id: {unpacked[1]}")
        return event_cls(data[hdrsize:])


tracemodule_event = {
    0x63: TraceModule,
}

