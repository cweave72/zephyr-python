import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ProtoRpcException(Exception):
    pass


class CallsetBase:

    def __init__(self, api):
        self.api = api[f"{self.name}_callset"]

    def check_reply(self, reply):
        if not reply.success:
            msg = (f"RPC {self.name}: call={reply.call_msg} "
                   f"returned status={reply.status_str}")
            logger.error(msg)
            raise ProtoRpcException(msg)
