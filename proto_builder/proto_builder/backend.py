import os
import tomllib
import yaml
import sys
from importlib import resources
from pathlib import Path
from setuptools import build_meta as _orig

from grpc_tools.protoc import main as protoc

# Base path to location of registry files.
REGISTRY_BASE = f"{str(Path.home())}/.local/share/protorpc/registry"


def find_protos(proto_base) -> list:
    p = Path(proto_base)
    files = p.rglob("*.proto")
    return list(files)


def find_proto(protos, proto_name) -> str:
    """Returns True if the proto_name is found in found_protos.
    """
    for proto in protos:
        stem = proto.stem.split('.')[0]
        if stem == proto_name:
            return proto

    return None


def create_registry(pkg_name, mod_name, cls_name, debug_prefix='proto_builder'):
    """Creates a registry file for the generated bindings for use by
    applications using the generated API.
    """
    # Create registry directory if it doesn't exist.
    Path(REGISTRY_BASE).mkdir(parents=True, exist_ok=True)

    entry = {
        "package": f"{pkg_name}.lib",
        "module": mod_name,
        "cls": cls_name,
    }

    REGISTRY_FILE = f"{REGISTRY_BASE}/{mod_name.lower()}.yaml"

    # Write the registry entry to the registry file.
    with open(REGISTRY_FILE, 'w') as f:
        yaml.dump(entry, f, indent=4)

    print(f"{debug_prefix} Registry file created at {REGISTRY_FILE} with entry: {entry}")


def generate_api(
    found_protos: list,
    proto: str,
    dest: Path,
    debug_prefix: str = 'proto_builder'
):
    """Generates python bindings for file proto.
    """
    includes = [f"-I{str(p.parents[0])}" for p in found_protos]

    grpc_tools_inc = (resources.files("grpc_tools") / "_proto").resolve()
    includes.append(f"-I{grpc_tools_inc}")

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


def run_builder():
    """Common entrypoint for all build hooks.
    """
    # 1. Load the per-package config from the project being installed
    with open("pyproject.toml", "rb") as f:
        config = tomllib.load(f).get("tool", {}).get("proto_builder", {})

    name = config['name'].lower()
    proto_file = config['proto_file']
    proto_mod = config.get('proto_mod', None)
    callset_name = config.get('callset_name', None)
    debug_prefix = f"[{name}]:"

    # Redirect stdout to a file to assist debugging during setuptools execution
    save_stdout = sys.stdout
    fp = open(f"{name}.log", 'w')
    sys.stdout = fp

    proto_base = os.environ.get("PROTO_BASE", None)
    if proto_base is None:
        print(f"{debug_prefix} Error: PROTO_BASE env variable not set.")
        raise Exception(f"{debug_prefix} PROTO_BASE env variable not set.")
    
    # 3. Your Centralized Boilerplate
    proto_files = find_protos(proto_base)

    print(f"{debug_prefix} Found protos: {list(proto_files)}")

    proto_path = find_proto(proto_files, proto_file)
    if proto_path is None:
        msg = f"{debug_prefix} Proto file {proto_file}.proto not found."
        raise Exception(f"{msg}")

    # The destination library where bindings will be generated.
    dest = Path.cwd().resolve() / f"{name}/lib"

    generate_api(proto_files, f"{proto_file}.proto", dest=dest, debug_prefix=debug_prefix)

    if all(v is not None for v in (proto_mod, callset_name)):
        print(f"{debug_prefix} Creating registery entry.")
        create_registry(name, proto_mod, callset_name)

    fp.close()
    sys.stdout = save_stdout


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    run_builder()
    return _orig.build_wheel(wheel_directory, config_settings, metadata_directory)

def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    run_builder()
    return _orig.build_editable(wheel_directory, config_settings, metadata_directory)

# Export standard hooks so the backend is compliant
get_requires_for_build_wheel = _orig.get_requires_for_build_wheel
prepare_metadata_for_build_wheel = _orig.prepare_metadata_for_build_wheel
build_sdist = _orig.build_sdist
