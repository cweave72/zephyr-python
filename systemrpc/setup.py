import sys
import os
import os.path as osp
import setuptools

from grpc_tools.protoc import main as protoc
from importlib import resources
from pathlib import Path

THIS_DIR = osp.dirname(__file__)

##############################################################################
NAME = "systemrpc"
PROTO_FILE = "SystemRpc"
DESC = ""
VERSION = "0.1.0"
##############################################################################

name_lower = NAME.lower()
debug_prefix = f"[{name_lower}]:"

# Redirect stdout to a file to assist debugging during setuptools execution
save_stdout = sys.stdout
fp = open(f"{name_lower}.log", 'w')
sys.stdout = fp


def find_protos():
    p = Path(PROTO_BASE)
    files = p.rglob("*.proto")
    return list(files)


def find_proto(protos, proto_name):
    """Returns True if the proto_name is found in found_protos.
    """
    for proto in protos:
        stem = proto.stem.split('.')[0]
        if stem == proto_name:
            return proto

    return None


def generate_api(found_protos, proto):
    """Generates python bindings for file proto.
    """
    includes = [f"-I{str(p.parents[0])}" for p in found_protos]

    grpc_tools_inc = (resources.files("grpc_tools") / "_proto").resolve()
    includes.append(f"-I{grpc_tools_inc}")

    # The destination library where bindings will be generated.
    dest = Path(THIS_DIR) / f"{NAME.lower()}/lib"
    # Create dest if it doesn't exist.
    dest.mkdir(exist_ok=True)

    cmd_args = []
    cmd_args.append("protoc")
    cmd_args.append(f"--python_betterproto_out={dest}")
    cmd_args += includes
    cmd_args.append(proto)

    print(f"{debug_prefix} protoc cmd = {cmd_args}")

    ret = protoc(cmd_args)
    if ret == 1:
        raise Exception(f"{debug_prefix} protoc returned 1: cmd_args={cmd_args}")


if os.environ.get("PROTO_BASE", None) is None:
    raise Exception(f"{debug_prefix} PROTO_BASE env variable not set.")

PROTO_BASE = os.environ["PROTO_BASE"]

proto_files = find_protos()

print(f"{debug_prefix} Found protos: {list(proto_files)}")

proto_path = find_proto(proto_files, PROTO_FILE)
if proto_path is None:
    msg = f"{debug_prefix} Proto file {PROTO_FILE}.proto not found."
    raise Exception(f"{msg}")

generate_api(proto_files, f"{PROTO_FILE}.proto")

setuptools.setup(
    name=NAME,
    version=VERSION,
)
