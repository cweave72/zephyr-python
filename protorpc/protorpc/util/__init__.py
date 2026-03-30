import sys
import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ProtoRpcException(Exception):
    pass


class CallsetBase:

    def __init__(self, api):
        self.api = api[f"{self.name}"]

    def check_reply(self, reply):
        if not reply.success:
            msg = (f"RPC {self.name}: call={reply.call_msg} "
                   f"returned status={reply.status_str}")
            logger.error(msg)
            raise ProtoRpcException(msg)

    @classmethod
    def check_version(cls, bindings: dict):
        """Checks the callset version against the API version.
        """
        # Extract the server-side callset version from the bindings.
        for _id in bindings:
            binding = bindings[_id]
            if binding['mod'] == cls.name:
                ver_major = binding['ver']['major']
                ver_minor = binding['ver']['minor']
                ver_patch = binding['ver']['patch']
                break
        else:
            msg = f"Callset {cls.name} not found in bindings."
            logger.error(msg)
            raise ProtoRpcException()

        if not hasattr(cls, 'version'):
            msg = f"Callset API {cls.name} does not have a version attribute."
            logger.error(msg)
            raise ProtoRpcException()

        # Extract the API version from the callset class.
        ver_parts = cls.version.split('.')
        api_ver_major, api_ver_minor, _ = map(int, ver_parts)

        if ver_major != api_ver_major:
            logger.error(f"Callset '{cls.name}' API incompatibility: "
                         f"Server-side callset version {ver_major}.{ver_minor}.{ver_patch} "
                         f"does not match python API version {cls.version}")
            raise ProtoRpcException()

        if ver_minor > api_ver_minor:
            logger.error(f"Callset '{cls.name}' API incompatibility: "
                         f"Server-side callset version {ver_major}."
                         f"{ver_minor}.{ver_patch} is newer than "
                         f"API version {cls.version}.")
            raise ProtoRpcException()

        if ver_minor < api_ver_minor:
            logger.warning(f"Server-side callset version {ver_major}."
                           f"{ver_minor}.{ver_patch} is older than "
                           f"API version {cls.version}. Not all API features "
                           "may be supported.")
