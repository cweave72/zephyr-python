import struct
import logging
from rich import inspect

from trace_tool import bytesToHexStr

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


class EventError(Exception):
    pass


def _hex32(a: int):
    return f"0x{a:08x}"


def _bytesstr(a: bytes):
    return a.decode('utf-8').strip('\x00')


class EventBase:
    """Base class for events."""

    fields = ()
    fields_conv = {
        "id": _hex32,
        "thread_id": _hex32,
        "thread_name": _bytesstr,
        "address": _bytesstr,
        "iface": _hex32,
        "pkt": _hex32,
    }

    def __init__(self, data: bytes):
        if self.fmt is None:
            self.size = 0
            self.data = bytearray()
            return
        self.size = struct.calcsize(self.fmt)
        self.data = data[: self.size]
        self.unpack()

    def unpack(self):
        unpacked = struct.unpack(self.fmt, self.data)
        try:
            for f, v in zip(self.fields, unpacked):
                if f in self.fields_conv:
                    setattr(self, f, self.fields_conv[f](v))
                else:
                    setattr(self, f, v)

        except Exception as e:
            raise EventError(f"{str(self)}: {str(e)}")

    def __str__(self):
        return self.__class__.__name__

    def __repr__(self):
        return f"Event({self.__dict__})"


class EventFrame:
    hdrfmt = f"{le}{uint32}{uint8}"
    fields = ("timestamp", "id")

    def __init__(self, data: bytes, ext_events=None):
        self.hdrsize = struct.calcsize(self.hdrfmt)
        self.hdr_data = data[: self.hdrsize]
        self.event = None
        self.success = False

        if ext_events is not None:
            self.events = {**core_events, **ext_events}
        else:
            self.events = core_events

        self.hdr_unpack()
        cls = self.events.get(self.id)
        if cls is None:
            msg = (f"Unknown event id: 0x{self.id:02x} "
                   f"hdr={bytesToHexStr(self.hdr_data)}")
            raise EventError(msg)
        self.event = cls(data[self.hdrsize :])
        self.event_frame_size = self.hdrsize + self.event.size
        self.success = True
        logger.debug(f"{repr(self)}, {repr(self.event)}")

    @classmethod
    def get_hdr_size(cls):
        """Gets the size of the header, in bytes."""
        return struct.calcsize(cls.hdrfmt)

    def hdr_unpack(self):
        unpacked = struct.unpack(self.hdrfmt, self.hdr_data)
        try:
            for f, v in zip(self.fields, unpacked):
                setattr(self, f, v)
        except Exception as e:
            logger.exception(f"{str(e)}")

    def __repr__(self):
        return (
            f"EventFrame(size={self.event_frame_size:3}, "
            f"time=0x{self.timestamp:08x}, type={str(self.event)})"
        )


class thread_switched_out(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class thread_switched_in(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class thread_priority_set(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}{int8}"
    fields = ("thread_id", "thread_name", "prio")


class thread_create(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class thread_abort(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class thread_suspend(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class thread_resume(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class thread_ready(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class thread_pending(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class thread_info(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}{uint32}{uint32}"
    fields = ("thread_id", "thread_name", "stack_base", "stack_size")


class thread_name_set(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class isr_enter(EventBase):
    fmt = None


class isr_exit(EventBase):
    fmt = None


class isr_exit_to_scheduler(EventBase):
    fmt = None


class idle(EventBase):
    fmt = None


class semaphore_init(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "ret")


class semaphore_give_enter(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class semaphore_give_exit(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class semaphore_take_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}"
    fields = ("id", "timeout")


class semaphore_take_exit(EventBase):
    fmt = f"{le}{uint32}{uint32}{int32}"
    fields = ("id", "timeout", "ret")


class semaphore_take_blocking(EventBase):
    fmt = f"{le}{uint32}{uint32}"
    fields = ("id", "timeout")


class semaphore_reset(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class mutex_init(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "ret")


class mutex_lock_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}"
    fields = ("id", "timeout")


class mutex_lock_blocking(EventBase):
    fmt = f"{le}{uint32}{uint32}"
    fields = ("id", "timeout")


class mutex_lock_exit(EventBase):
    fmt = f"{le}{uint32}{uint32}{int32}"
    fields = ("id", "timeout", "ret")


class mutex_unlock_enter(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class mutex_unlock_exit(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class timer_init(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class timer_start(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}"
    fields = ("id", "duration", "period")


class timer_stop(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class timer_status_sync_enter(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class timer_status_sync_blocking(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class timer_status_sync_exit(EventBase):
    fmt = f"{le}{uint32}{uint32}"
    fields = ("id", "result")


class user_mode_enter(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class thread_wakeup(EventBase):
    fmt = f"{le}{uint32}{ctf_str20}"
    fields = ("thread_id", "thread_name")


class socket_init(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}{uint32}"
    fields = ("id", "family", "type", "proto")


class socket_close_enter(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class socket_close_exit(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class socket_shutdown_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}"
    fields = ("id", "how")


class socket_shutdown_exit(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "result")


class socket_bind_enter(EventBase):
    fmt = f"{le}{uint32}{ctf_str46}{uint32}{uint16}"
    fields = ("id", "address", "address_len", "port")


class socket_bind_exit(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "result")


class socket_connect_enter(EventBase):
    fmt = f"{le}{uint32}{ctf_str46}{uint32}"
    fields = ("id", "address", "address_len")


class socket_connect_exit(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "result")


class socket_listen_enter(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "backlog")


class socket_listen_exit(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "result")


class socket_accept_enter(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class socket_accept_exit(EventBase):
    fmt = f"{le}{uint32}{ctf_str46}{uint32}{uint16}{int32}"
    fields = ("id", "address", "address_len", "port", "result")


class socket_sendto_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}{ctf_str46}{uint32}"
    fields = ("id", "data_length", "flags", "address", "address_len")


class socket_sendto_exit(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "result")


class socket_sendmsg_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}{ctf_str46}{uint32}"
    fields = ("id", "flags", "msghdr", "address", "data_length")


class socket_sendmsg_exit(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "result")


class socket_recvfrom_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}{uint32}{uint32}"
    fields = ("id", "max_length", "flags", "address_int", "address_len")


class socket_recvfrom_exit(EventBase):
    fmt = f"{le}{uint32}{ctf_str46}{uint32}{int32}"
    fields = ("id", "address", "address_len", "result")


class socket_recvmsg_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}{int32}"
    fields = ("id", "msg", "max_msg_length", "result")


class socket_recvmsg_exit(EventBase):
    fmt = f"{le}{uint32}{uint32}{ctf_str46}{int32}"
    fields = ("id", "msg_length", "address", "result")


class socket_fcntl_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}"
    fields = ("id", "cmd", "flags")


class socket_fcntl_exit(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "result")


class socket_ioctl_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}"
    fields = ("id", "request")


class socket_ioctl_exit(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "result")


class socket_poll_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}{int32}"
    fields = ("fds", "num_fds", "timeout")


class socket_poll_value(EventBase):
    fmt = f"{le}{int32}{uint16}"
    fields = ("fd", "events")


class socket_poll_exit(EventBase):
    fmt = f"{le}{uint32}{uint32}{int32}"
    fields = ("fds", "num_fds", "result")


class socket_getsockopt_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}"
    fields = ("id", "level", "optname")


class socket_getsockopt_exit(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}{uint32}{uint32}{int32}"
    fields = ("id", "level", "optname", "optval", "optlen", "result")


class socket_setsockopt_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}{uint32}{uint32}"
    fields = ("id", "level", "optname", "optval", "optlen")


class socket_setsockopt_exit(EventBase):
    fmt = f"{le}{uint32}{int32}"
    fields = ("id", "result")


class socket_getpeername_enter(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class socket_getpeername_exit(EventBase):
    fmt = f"{le}{uint32}{ctf_str46}{uint32}{int32}"
    fields = ("id", "address", "address_len", "result")


class socket_getsockname_enter(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("id",)


class socket_getsockname_exit(EventBase):
    fmt = f"{le}{uint32}{ctf_str46}{uint32}{int32}"
    fields = ("id", "address", "address_len", "result")


class socket_socketpair_enter(EventBase):
    fmt = f"{le}{uint32}{uint32}{uint32}{uint32}"
    fields = ("family", "type", "proto", "sv")


class socket_socketpair_exit(EventBase):
    fmt = f"{le}{int32}{int32}{int32}"
    fields = ("socket0", "socket1", "result")


class net_recv_data_enter(EventBase):
    fmt = f"{le}{int32}{uint32}{uint32}{uint32}"
    fields = ("if_index", "iface", "pkt", "pkt_len")


class net_recv_data_exit(EventBase):
    fmt = f"{le}{int32}{uint32}{uint32}{int32}"
    fields = ("if_index", "iface", "pkt", "result")


class net_send_data_enter(EventBase):
    fmt = f"{le}{int32}{uint32}{uint32}{uint32}"
    fields = ("if_index", "iface", "pkt", "pkt_len")


class net_send_data_exit(EventBase):
    fmt = f"{le}{int32}{uint32}{uint32}{int32}"
    fields = ("if_index", "iface", "pkt", "result")


class net_rx_time(EventBase):
    fmt = f"{le}{int32}{uint32}{uint32}{uint32}{uint32}{uint32}"
    fields = (
        "if_index",
        "iface",
        "pkt",
        "priority",
        "traffic_class",
        "duration_us",
    )


class net_tx_time(EventBase):
    fmt = f"{le}{int32}{uint32}{uint32}{uint32}{uint32}{uint32}"
    fields = (
        "if_index",
        "iface",
        "pkt",
        "priority",
        "traffic_class",
        "duration_us",
    )


class named_event(EventBase):
    fmt = f"{le}{ctf_str20}{uint32}{uint32}"
    fields = ("name", "arg0", "arg1")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.name.decode()

class user_0(EventBase):
    fmt = f"{le}{uint32}"
    fields = ("el",)


# Event IDs
core_events = {
    0x10: thread_switched_out,
    0x11: thread_switched_in,
    0x12: thread_priority_set,
    0x13: thread_create,
    0x14: thread_abort,
    0x15: thread_suspend,
    0x16: thread_resume,
    0x17: thread_ready,
    0x18: thread_pending,
    0x19: thread_info,
    0x1A: thread_name_set,
    0x1B: isr_enter,
    0x1C: isr_exit,
    0x1D: isr_exit_to_scheduler,
    0x1E: idle,
    0x1F: None, #"ID_START_CALL",
    0x20: None, #"ID_END_CALL",
    0x21: semaphore_init,
    0x22: semaphore_give_enter,
    0x23: semaphore_give_exit,
    0x24: semaphore_take_enter,
    0x25: semaphore_take_blocking,
    0x26: semaphore_take_exit,
    0x27: semaphore_reset,
    0x28: mutex_init,
    0x29: mutex_lock_enter,
    0x2A: mutex_lock_blocking,
    0x2B: mutex_lock_exit,
    0x2C: mutex_unlock_enter,
    0x2D: mutex_unlock_exit,
    0x2E: timer_init,
    0x2F: timer_start,
    0x30: timer_stop,
    0x31: timer_status_sync_enter,
    0x32: timer_status_sync_blocking,
    0x33: timer_status_sync_exit,
    0x34: user_mode_enter,
    0x35: thread_wakeup,
    0x36: socket_init,
    0x37: socket_close_enter,
    0x38: socket_close_exit,
    0x39: socket_shutdown_enter,
    0x3A: socket_shutdown_exit,
    0x3B: socket_bind_enter,
    0x3C: socket_bind_exit,
    0x3D: socket_connect_enter,
    0x3E: socket_connect_exit,
    0x3F: socket_listen_enter,
    0x40: socket_listen_exit,
    0x41: socket_accept_enter,
    0x42: socket_accept_exit,
    0x43: socket_sendto_enter,
    0x44: socket_sendto_exit,
    0x45: socket_sendmsg_enter,
    0x46: socket_sendmsg_exit,
    0x47: socket_recvfrom_enter,
    0x48: socket_recvfrom_exit,
    0x49: socket_recvmsg_enter,
    0x4A: socket_recvmsg_exit,
    0x4B: socket_fcntl_enter,
    0x4C: socket_fcntl_exit,
    0x4D: socket_ioctl_enter,
    0x4E: socket_ioctl_exit,
    0x4F: socket_poll_enter,
    0x50: socket_poll_value,
    0x51: socket_poll_exit,
    0x52: socket_getsockopt_enter,
    0x53: socket_getsockopt_exit,
    0x54: socket_setsockopt_enter,
    0x55: socket_setsockopt_exit,
    0x56: socket_getpeername_enter,
    0x57: socket_getpeername_exit,
    0x58: socket_getsockname_enter,
    0x59: socket_getsockname_exit,
    0x5A: socket_socketpair_enter,
    0x5B: socket_socketpair_exit,
    0x5C: net_recv_data_enter,
    0x5D: net_recv_data_exit,
    0x5E: net_send_data_enter,
    0x5F: net_send_data_exit,
    0x60: net_rx_time,
    0x61: net_tx_time,
    0x62: named_event,
    0x99: user_0,
}
