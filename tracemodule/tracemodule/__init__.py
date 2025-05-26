import struct
import trace_tool.fmt_types as ft

from .trace_protorpc import ProtoRpcModule

# Header added by the trace module event type.
tracemodule_hdr_fmt = f"{ft.le}{ft.uint8}{ft.uint8}"
tracemodule_hdr_fmt_size = struct.calcsize(tracemodule_hdr_fmt)

class TraceModuleError(Exception):
    pass


class TraceModule:
    """Class which provides the TraceModule header definition and module
    classes.
    """
    fmt = tracemodule_hdr_fmt
    fields = (
        "module_id",
        "event_id"
    )

    # Table of module classes. Provided by user.
    modules = {}

    def __new__(cls, data: bytes):
        """Dynamic event class creation.
        """
        hdrsize = struct.calcsize(cls.fmt)
        unpacked = struct.unpack(cls.fmt, data[:hdrsize])
        module_cls = cls.modules.get(unpacked[0])
        if module_cls is None:
            raise TraceModuleError(f"Unknown module id: {unpacked[0]}")
        event_cls = module_cls.events.get(unpacked[1])
        if event_cls is None:
            raise TraceModuleError(f"Module: {str(module_cls())}, "
                                   f"unknown event id: {unpacked[1]}")
        
        # Instantiate the event class (which has an EventBase base class) and
        # add the tracemodule header size to the event size.
        event = event_cls(data[hdrsize:])
        event.size += tracemodule_hdr_fmt_size

        #return the event instance.
        return event

    @classmethod
    def register_module(cls, module_id, module_cls):
        """Registers a module with the class.
        """
        cls.modules[module_id] = module_cls


# Module IDs (Must match definitins in firmware).
PROTORPC_ID = 1


# Add module classes with corresponding module IDs.
TraceModule.modules[PROTORPC_ID] = ProtoRpcModule

# Entrypoint for the Trace Module into table of ctf events.
# The tracemodule abstracts non-core events behind a single event id.
tracemodule_event = {
    0x63: TraceModule,
}

