import trace_tool.fmt_types as ft
from trace_tool.ctf_config import EventBase


int8      = ft.int8     
uint8     = ft.uint8    
uint32    = ft.uint32   
int32     = ft.int32    
uint16    = ft.uint16   
int16     = ft.int16    
ctf_str20 = ft.ctf_str20
ctf_str46 = ft.ctf_str46

# Event classes within this module

class protorpc_header(EventBase):
    fmt = f"{uint32}{uint32}{uint32}"
    fields = (
        "seqn",
        "which_callset",
        "which_msg",
    )


# The overall module class.

class ProtoRpcModule:
    "Class which maps module events to corresponding classes."

    # Table of events provided by this module.
    events = {
        0x01: protorpc_header,
    }

    def __str__(self):
        return self.__class__.__name__

