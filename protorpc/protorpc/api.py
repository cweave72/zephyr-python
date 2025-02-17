import sys
import time
import datetime
import logging
import typing as t

from dataclasses import dataclass, fields
from rich import inspect

from google.protobuf.internal import encoder, decoder

logger = logging.getLogger(__name__)

FrameDict = {}


@dataclass
class MsgArg:
    name: str
    group: str
    proto_type: str
    number: int


@dataclass
class FrameMsg:
    name: str
    cls: t.Any
    args: t.List[MsgArg]


@dataclass
class FrameCallset:
    name: str
    id: int
    cls: t.Any
    msgs: t.Dict


def get_field_metadata(field):
    """Helper function to extract field metadata from betterproto.
    """
    meta = field.metadata.get('betterproto')
    return dict(
        group=meta.group,
        proto_type=meta.proto_type,
        number=meta.number,
    )


def encode_varint(value):
    """Encodes a value as a protobuf Varint.
    Returns a bytearray.
    """
    return encoder._VarintBytes(value)


def decode_varint(encoded):
    """Decodes an encoded Varint to an integer value.
    """
    return decoder._DecodeVarint(encoded, 0)


def parse_callset_fields(
    cls_curr,
    cs_id=None,
    cs_curr=None,
    msg_curr=None,
    is_top=True
):
    """Recursively process a protobuf callset, creating FrameDict global.
    Params:
        cls_curr : First call should be the Callset class to be processed.
        cs_id    : First call should provide the callset ID.
        cs_curr  : For internal recursive use only.
        msg_curr : For internal recursive use only.
        is_top   : Do not use, by default indicates this was the first call.

    Call per callset:
        parse_callset_field(Callset0, cs_id=0)
        parse_callset_field(Callset1, cs_id=1)
        ...
        parse_callset_field(CallsetN, cs_id=2)
    """
    global FrameDict

    logger.debug("======================================")
    logger.debug(f"cls_curr={cls_curr}")
    logger.debug(f"msg_curr={msg_curr}")

    if is_top:
        # Top-level call. Recurse from here, then done.
        logger.debug(f"Parsing callset: {cls_curr.__name__}")
        cs_name = cls_curr.__name__
        cs_inst = FrameCallset(name=cs_name, id=cs_id, cls=cls_curr, msgs={})
        FrameDict[cs_name] = cs_inst
        parse_callset_fields(cls_curr,
                             cs_curr=cs_inst,
                             msg_curr=None,
                             is_top=False)
        return

    for field in fields(cls_curr):
        meta = get_field_metadata(field)
        #logger.debug(f"field: {field.name}; meta: {meta}")

        if meta.get('proto_type') == 'message':
            field_cls = cls_curr._cls_for(field)

            if meta.get('group') == 'msg':
                m_inst = FrameMsg(name=field.name, cls=field_cls, args=[])
                cs_curr.msgs[field.name] = m_inst
                parse_callset_fields(field_cls,
                                     cs_curr=cs_curr,
                                     msg_curr=m_inst,
                                     is_top=False)
            else:
                m_inst = FrameMsg(name=field.name, cls=field_cls, args=[])
                parse_callset_fields(field_cls,
                                     cs_curr=None,
                                     msg_curr=m_inst,
                                     is_top=False)

            continue

        logger.debug(f"Adding arg to msg={msg_curr.name}: field={field.name}")

        kwargs = {**{'name': field.name}, **meta}
        msg_curr.args.append(MsgArg(**kwargs))


class Request:
    """RPC request class.
    """

    def __init__(
        self,
        conn,
        header_cls,
        callset_id,
        callset_name,
        callset_cls,
        msg_name,
        msg_inst,
        **kwargs
    ):
        self.no_reply = kwargs.pop('no_reply', False)
        self.conn = conn
        self.callset = callset_cls()
        self.callset_id = callset_id
        self.header = header_cls()
        self.reply = Reply(header_cls, callset_cls, msg_name, msg_inst)
        self.got_reply = False
        self.timedout = False

        self.msg_name = msg_name
        self.msg_inst = msg_inst

        # Set message instance to the frame callset attribute.
        setattr(self.callset, msg_name, msg_inst)

    def send(self, timeout=3):
        """Sends a serialized RPC frame using the underlying connection object.
        """
        self.header.seqn = self.conn.get_next_seqn()
        self.header.no_reply = self.no_reply
        self.header.which_callset = self.callset_id

        # Encode header
        hdr_ser = self.header.SerializeToString()
        hdr_delim = encode_varint(len(hdr_ser))
        hdr_ser_bytes = hdr_delim + hdr_ser
        #logger.debug(f"hdr_ser length: {len(hdr_ser)} --> {hdr_delim}")
        #logger.debug(f"hdr_ser_bytes: {hdr_ser_bytes}")

        # Encode callset
        callset_ser = self.callset.SerializeToString()
        callset_delim = encode_varint(len(callset_ser))
        callset_ser_bytes = callset_delim + callset_ser
        #logger.debug(f"callset_ser length: {len(callset_ser)} --> {callset_delim}")
        #logger.debug(f"callset_ser_bytes: {callset_ser_bytes}")

        # The complete frame bytes
        ser = hdr_ser_bytes + callset_ser_bytes
        logger.debug(f"frame bytes: {ser}")

        self.ttl = datetime.datetime.now() + datetime.timedelta(seconds=timeout)
        logger.debug(f"sending header: {self.header}")
        logger.debug(f"sending request: {self.callset}")
        self.conn.write(ser)

        if self.no_reply:
            return
        else:
            self.conn.add_pending(self)

    def send_sync(self, timeout=3):
        """Sends and waits for success or timeout.
        """
        self.send(timeout)

        if not self.no_reply:
            while True:
                time.sleep(0.1)
                if self.timedout:
                    self.reply.set_timedout()
                    return
                if self.got_reply:
                    return

    @property
    def seqn(self):
        """Gets the frame seqn.
        """
        return self.header.seqn


class Reply:
    """RPC reply class.
    """

    def __init__(self, header_cls, callset_cls, call_msg_name, call_msg_inst):
        # Save references to the call msg and instance.
        self.callset = callset_cls()
        self.call_msg = call_msg_name
        self.call_msg_inst = call_msg_inst
        self.header = header_cls()
        self.result = None
        self.success = False
        self.timedout = False

    def rcv_header(self, data):
        """Parses raw received frame into header instance.
        Returns the position of the callset varint.
        """
        try:
            header_len, pos = decode_varint(data)
            self.header.parse(data[pos:pos+header_len])
            logger.debug(f"Parsed header={self.header}")
        except Exception as e:
            logger.exception(f"Error on header parse: {str(e)}")
            raise e
        return pos + header_len

    def rcv_handler(self, data, pos):
        """Parses raw received frame into class instance.
        Params:
            data: The entire received frame.
            pos: The position of the callset varint delimiter.
        """
        try:
            callset_len, i = decode_varint(data[pos:])
            callset_start = pos + i
            #logger.debug(f"callset_len={callset_len}, callset_start={callset_start}")
            self.callset.parse(data[callset_start:])
            logger.debug(f"Decoded callset reply ({self.status_str}): {self.callset}")
            if self.status in [0, 3]:
                self.success = True if self.status == 0 else False
                self.result = self.get_reply_value()
        except Exception as e:
            logger.exception(f"Error on frame parse: {str(e)}")
            raise e

    def get_reply_value(self):
        """Retrieves the message from the recieved frame based on which
        message was received.
        """
        which_msg = self.callset._group_current['msg']
        logger.debug(f"reply: which_msg={which_msg}")
        reply_value = getattr(self.callset, which_msg)
        return reply_value

    def set_timedout(self):
        self.timedout = True

    def exit_on_fail(self, on_exit_func=None):
        """Checks the return code and exits on failure.
        """
        if self.timedout or not self.success:
            logger.error(f"RPC error: {self.status_str}")
            if on_exit_func is not None:
                on_exit_func()
            sys.exit(1)

    @property
    def seqn(self):
        """Gets the reply frame seqn.
        """
        return self.header.seqn

    @property
    def status(self):
        """Gets the reply status from the header.
        """
        return self.header.status

    @property
    def status_str(self):
        if self.timedout:
            return "REQUEST TIMEOUT"

        status_str = {
            0: "SUCCESS",
            1: "BAD_RESOLVER_LOOKUP",
            2: "BAD_CALLSET_UNPACK",
            3: "BAD_HANDLER_LOOKUP",
            4: "HANDLER_ERROR",
        }.get(self.status, 'UNDEFINED')
        return status_str


def call_factory(
    conn,
    header_cls: t.Any,
    callset_id: int,
    callset_name: str,
    callset_cls: t.Any,
    msg_name: str,
    msg_cls: t.Any
):
    def call_func(*args, **kwargs):
        no_reply = kwargs.pop('no_reply', False)
        msg_inst = msg_cls(*args, **kwargs)
        req = Request(conn,
                      header_cls,
                      callset_id,
                      callset_name,
                      callset_cls,
                      msg_name,
                      msg_inst,
                      no_reply=no_reply)
        req.send_sync()
        return req.reply
    call_func.__name__ = msg_name.rstrip('_call')
    return call_func


class Api:
    """RPC frame api class for a callset. Methods are callset functions.
    """

    def __init__(self, header_cls, callset: FrameCallset, conn) -> None:

        self.header_cls = header_cls
        self.callset_name = callset.name
        self.callset_cls = callset.cls
        self.callset_id = callset.id
        self.conn = conn

        for msg, frame_msg in callset.msgs.items():
            setattr(self, msg, frame_msg)

            if not msg.endswith('_call'):
                continue

            func = call_factory(self.conn,
                                self.header_cls,
                                self.callset_id,
                                self.callset_name,
                                self.callset_cls,
                                msg,
                                frame_msg.cls)
            setattr(self, func.__name__, func)
