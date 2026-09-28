"""Converts a .pb.yaml file to a raw protobuf blob, and a blob to YAML.

A .pb.yaml file describes one protobuf message:

    proto: NetConf           # the proto file stem
    message: NetConf         # the message name in the proto package
    out: net.pb              # optional; default: the file name minus .yaml
    data:                    # the message fields, by proto field name
      ipv4:
        mode: IPV4_MODE_STATIC
        address: 192.168.1.16

The blob is the raw message, with no header. The firmware decodes it with
FsApi_unpack_file into the nanopb struct of the same .proto.

ProtoLib compiles each proto one time for each run: betterproto bindings for
the encoding, and a descriptor set for the nanopb checks (nanopb.py).
"""
from __future__ import annotations

import contextlib
import dataclasses
import importlib
import importlib.util
import io
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any, Iterable, Iterator, Optional

import betterproto
import yaml
from betterproto.compile.naming import pythonize_class_name
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from grpc_tools.protoc import main as protoc
from proto_builder.backend import find_protos, generate_api

from fsapi.brand import BLOB_SUFFIX, nanopb

logger = logging.getLogger(__name__)

HEADER_KEYS = ("proto", "message", "out", "data")


class BlobError(Exception):
    """A .pb.yaml file or a blob is not correct, or a proto does not compile.

    The message starts with the file name and tells the user what to do.
    """


@dataclass
class Blob:
    """One rendered blob.

    Attributes:
        yaml_path: The source .pb.yaml file.
        out: The output path, relative to the directory of yaml_path.
        data: The serialized message.
    """
    yaml_path: Path
    out: PurePosixPath
    data: bytes


@dataclass
class MessageType:
    """A message type of a compiled proto.

    Attributes:
        proto: The proto file stem, for example 'NetConf'.
        name: The message name in the package, for example 'NetConf'.
        cls: The betterproto message class.
        info: The message definition in the descriptor set.
        index: The descriptor index of the proto and its imports.
    """
    proto: str
    name: str
    cls: type[betterproto.Message]
    info: nanopb.MessageInfo
    index: nanopb.DescriptorIndex


@contextlib.contextmanager
def _capture_stderr() -> Iterator[None]:
    """Captures the stderr output of protoc and its plugins.

    protoc runs in this process and writes to file descriptor 2. The
    betterproto plugin writes 'Writing ...' lines there. The function writes
    the captured text to stderr only if the block raises an exception, so a
    protoc error is still visible.

    Yields:
        None. The block runs with file descriptor 2 redirected.
    """
    sys.stderr.flush()
    saved = os.dup(2)
    with tempfile.TemporaryFile() as tmp:
        os.dup2(tmp.fileno(), 2)
        try:
            yield
        except BaseException:
            os.dup2(saved, 2)
            tmp.seek(0)
            sys.stderr.write(tmp.read().decode(errors="replace"))
            raise
        finally:
            os.dup2(saved, 2)
            os.close(saved)


def proto_search_path(extra: Iterable[str | Path] = ()) -> list[Path]:
    """Makes the proto search path: $PROTO_BASE, then each extra directory.

    Args:
        extra: More directories, for example the proto/ directory of an
            application. A directory which does not exist is skipped.

    Returns:
        The existing directories, with no duplicates, in search order.
    """
    dirs: list[Path] = []
    base = os.environ.get("PROTO_BASE")
    for d in ([base] if base else []) + [str(e) for e in extra]:
        p = Path(d).resolve()
        if p.is_dir() and p not in dirs:
            dirs.append(p)
    return dirs


class ProtoLib:
    """Compiles protos on demand and caches the result for the run."""

    def __init__(self, search_path: list[Path], work_dir: Path) -> None:
        """Finds all protos in the search path.

        Args:
            search_path: The proto directories, from proto_search_path.
            work_dir: A scratch directory for the generated code. The
                object deletes and writes subdirectories of it.

        Raises:
            BlobError: The search path is empty.
        """
        if not search_path:
            raise BlobError("The proto search path is empty. Set PROTO_BASE "
                            "(source workspace-env.sh) or use --proto-path.")
        self.search_path = search_path
        self.work_dir = work_dir
        self.found: list[Path] = []
        for d in search_path:
            self.found += find_protos(d)
        self._types: dict[tuple[str, str], MessageType] = {}
        self._modules: dict[str, tuple[ModuleType, nanopb.DescriptorIndex,
                                       str]] = {}

    def _includes(self) -> list[str]:
        """Makes the protoc include options, as generate_api does.

        Returns:
            One -I option for the directory of each found proto, and one for
            the protos of grpc_tools (google/protobuf/*.proto).
        """
        dirs: list[str] = []
        for p in self.found:
            d = str(p.parent)
            if d not in dirs:
                dirs.append(d)
        dirs.append(str((resources.files("grpc_tools") / "_proto").resolve()))
        return [f"-I{d}" for d in dirs]

    def _protoc(self, args: list[str], what: str) -> None:
        """Runs protoc.

        Args:
            args: The protoc arguments after the include options.
            what: A description of the step, for the error text.

        Raises:
            BlobError: protoc failed. protoc writes the cause to stderr.
        """
        with _capture_stderr():
            if protoc(["protoc"] + self._includes() + args) != 0:
                raise BlobError(f"protoc failed: {what}. See the protoc "
                                f"message above.")

    def _find(self, stem: str) -> Path:
        """Finds one proto file by its stem.

        Args:
            stem: The proto file stem, for example 'NetConf'.

        Returns:
            The path of the proto file.

        Raises:
            BlobError: No proto, or more than one proto, has the stem.
        """
        matches = [p for p in self.found if p.stem == stem]
        if not matches:
            dirs = ", ".join(str(d) for d in self.search_path)
            raise BlobError(f"No proto file {stem}.proto in the search path "
                            f"({dirs}).")
        if len(matches) > 1:
            names = ", ".join(str(p) for p in matches)
            raise BlobError(f"More than one {stem}.proto in the search path: "
                            f"{names}.")
        return matches[0]

    def _nanopb_pb2(self) -> ModuleType:
        """Compiles nanopb.proto to Python and loads it (one time).

        Returns:
            The nanopb_pb2 module.

        Raises:
            BlobError: The search path has no nanopb.proto, or protoc failed.
        """
        out = self.work_dir / "nanopb"
        py_file = out / "nanopb_pb2.py"
        if not py_file.is_file():
            nanopb_proto = self._find("nanopb")
            out.mkdir(parents=True, exist_ok=True)
            self._protoc([f"--python_out={out}", str(nanopb_proto)],
                         "nanopb.proto to Python")
        return nanopb.load_nanopb_pb2(py_file)

    def _compile(self, stem: str) -> tuple[ModuleType, nanopb.DescriptorIndex,
                                           str]:
        """Compiles one proto: betterproto bindings and a descriptor set.

        Args:
            stem: The proto file stem.

        Returns:
            A tuple (bindings module of the proto package, descriptor index,
            proto package name).

        Raises:
            BlobError: The proto does not exist or does not compile.
        """
        if stem in self._modules:
            return self._modules[stem]
        proto = self._find(stem)
        options = proto.with_suffix(".options")
        if options.is_file():
            logger.warning(f"{options}: the blob checks do not read .options "
                           f"files. Use [(nanopb)...] options in the .proto.")

        # The nanopb extensions must be registered before the parse of the
        # descriptor set, or the parse drops the option values.
        pb2 = self._nanopb_pb2()
        desc_file = self.work_dir / f"{stem}.desc"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._protoc([f"--descriptor_set_out={desc_file}", "--include_imports",
                      str(proto)], f"{proto.name} descriptor set")
        fds = FileDescriptorSet.FromString(desc_file.read_bytes())
        index = nanopb.DescriptorIndex(fds, pb2)
        package = fds.file[-1].package

        dest = self.work_dir / "py" / stem
        dest.parent.mkdir(parents=True, exist_ok=True)
        # protoc finds the betterproto plugin (protoc-gen-python_betterproto)
        # in PATH. The plugin is in the bin directory of this interpreter,
        # which is not in PATH when make runs the tool from the venv.
        bin_dir = str(Path(sys.executable).parent)
        path = os.environ.get("PATH", "")
        log = io.StringIO()
        try:
            os.environ["PATH"] = bin_dir + os.pathsep + path
            with contextlib.redirect_stdout(log), _capture_stderr():
                generate_api(self.found, str(proto), dest, debug_prefix="[pbblob]")
        except Exception as e:
            raise BlobError(f"protoc failed: {proto.name} betterproto "
                            f"bindings. See the protoc message above.") from e
        finally:
            os.environ["PATH"] = path
            logger.debug(log.getvalue())
        module = _import_bindings(dest, stem, package)

        self._modules[stem] = (module, index, package)
        return self._modules[stem]

    def message_type(self, proto: str, message: str) -> MessageType:
        """Gets a message type of a proto. Compiles the proto on first use.

        Args:
            proto: The proto file stem, for example 'NetConf'.
            message: The message name, for example 'NetConf'. The name can
                include the package ('netconf.NetConf') and enclosing
                messages ('Outer.Inner').

        Returns:
            The message type.

        Raises:
            BlobError: The proto does not compile, or it has no such
                message.
        """
        key = (proto, message)
        if key in self._types:
            return self._types[key]
        module, index, package = self._compile(proto)
        name = message
        if package and name.startswith(f"{package}."):
            name = name[len(package) + 1:]
        info = index.find_message(f"{package}.{name}" if package else name)
        if info is None or info.desc.options.map_entry:
            raise BlobError(f"{proto}.proto has no message '{message}'. "
                            f"Messages: {', '.join(index.message_names(package))}.")
        cls = _find_class(module, name)
        self._types[key] = MessageType(proto, name, cls, info, index)
        return self._types[key]


def _import_bindings(dest: Path, stem: str, package: str) -> ModuleType:
    """Imports the betterproto bindings of one proto.

    The function loads dest as a private top-level package. The proto
    package name then cannot collide with an installed package, for example
    the fsapi package of FsApiRpc.proto.

    Args:
        dest: The output directory of generate_api.
        stem: The proto file stem. It makes the private package name.
        package: The proto package, for example 'netconf'.

    Returns:
        The module of the proto package.
    """
    root = "_pbblob_" + re.sub(r"\W", "_", stem)
    init = dest / "__init__.py"
    if not init.exists():
        init.write_text("")
    for name in [m for m in sys.modules if m == root or m.startswith(root + ".")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        root, init, submodule_search_locations=[str(dest)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[root] = module
    spec.loader.exec_module(module)
    return importlib.import_module(f"{root}.{package}") if package else module


def _find_class(module: ModuleType, name: str) -> type[betterproto.Message]:
    """Finds the betterproto class of a message.

    Args:
        module: The bindings module of the proto package.
        name: The message name in the package. A nested message has the
            form 'Outer.Inner'.

    Returns:
        The message class.

    Raises:
        BlobError: The module has no class for the message.
    """
    parts = name.split(".")
    for candidate in (pythonize_class_name("".join(parts)),
                      pythonize_class_name("_".join(parts))):
        cls = getattr(module, candidate, None)
        if isinstance(cls, type) and issubclass(cls, betterproto.Message):
            return cls
    raise BlobError(f"The bindings have no class for message '{name}'.")


def load_yaml(yaml_path: Path) -> dict[str, Any]:
    """Reads a .pb.yaml file and checks its header.

    Args:
        yaml_path: The .pb.yaml file.

    Returns:
        The YAML content: proto, message, data, and out if present.

    Raises:
        BlobError: The file is not valid YAML, or the header is not correct.
    """
    try:
        doc = yaml.safe_load(yaml_path.read_text())
    except yaml.YAMLError as e:
        raise BlobError(f"{yaml_path}: not valid YAML: {e}") from e
    if not isinstance(doc, dict):
        raise BlobError(f"{yaml_path}: the file must be a mapping with the "
                        f"keys proto, message and data.")
    unknown = [k for k in doc if k not in HEADER_KEYS]
    if unknown:
        raise BlobError(f"{yaml_path}: unknown key(s) {', '.join(map(str, unknown))}. "
                        f"Keys: {', '.join(HEADER_KEYS)}.")
    for key in ("proto", "message"):
        if not isinstance(doc.get(key), str) or not doc[key]:
            raise BlobError(f"{yaml_path}: '{key}' is missing or not a string.")
    return doc


def out_path(yaml_path: Path, out: Optional[str]) -> PurePosixPath:
    """Gets the output path of a blob.

    Args:
        yaml_path: The .pb.yaml file.
        out: The 'out' value of the file, or None.

    Returns:
        The output path, relative to the directory of yaml_path. The
        default is the file name minus '.yaml', for example 'net.pb'.

    Raises:
        BlobError: 'out' is absolute, goes up with '..', or ends with
            '.pb.yaml'.
    """
    if out is None:
        return PurePosixPath(yaml_path.name[:-len(".yaml")])
    p = PurePosixPath(str(out))
    if p.is_absolute() or ".." in p.parts or not p.name:
        raise BlobError(f"{yaml_path}: out '{out}' must be a relative path "
                        f"in the directory of the file.")
    if p.name.endswith(BLOB_SUFFIX):
        raise BlobError(f"{yaml_path}: out '{out}' cannot end with "
                        f"{BLOB_SUFFIX}.")
    return p


def render(yaml_path: Path, lib: ProtoLib) -> Blob:
    """Converts a .pb.yaml file to a raw protobuf blob.

    Args:
        yaml_path: The .pb.yaml file.
        lib: The proto library of the run.

    Returns:
        The blob: output path and serialized message.

    Raises:
        BlobError: The file, the data or the proto is not correct. For data
            errors, the message lists each problem.
    """
    doc = load_yaml(yaml_path)
    out = out_path(yaml_path, doc.get("out"))
    try:
        mtype = lib.message_type(doc["proto"], doc["message"])
    except BlobError as e:
        raise BlobError(f"{yaml_path}: {e}") from e

    data = doc.get("data") or {}
    errors = nanopb.check_message(mtype.index, mtype.info, data)
    if errors:
        lines = "\n".join(f"  {e}" for e in errors)
        raise BlobError(f"{yaml_path}: {len(errors)} error(s) in data "
                        f"({mtype.proto}.{mtype.name}):\n{lines}")
    try:
        blob = bytes(mtype.cls().from_dict(data))
    except Exception as e:
        raise BlobError(f"{yaml_path}: cannot serialize {mtype.name}: "
                        f"{type(e).__name__}: {e}") from e
    return Blob(yaml_path, out, blob)


def _prune(msg: betterproto.Message, doc: dict[str, Any]) -> None:
    """Removes the fields which are not set from a to_dict result.

    to_dict(include_default_values=True) writes all fields. Two kinds of
    field are wrong in the YAML:

    - Each member of a oneof. The YAML then sets more than one member, and
      render rejects it. The function keeps only the member which is set.
    - A submessage which is not in the blob. The YAML then sets an empty
      submessage, and nanopb sets its has_<field> flag. The function removes
      the submessage.

    The function processes msg and each submessage.

    Args:
        msg: The decoded message.
        doc: The to_dict result of msg, in snake case. The function changes
            it in place.
    """
    for group in msg._betterproto.oneof_field_by_group:
        chosen, _ = betterproto.which_one_of(msg, group)
        for field in msg._betterproto.oneof_field_by_group[group]:
            if field.name != chosen:
                doc.pop(field.name.rstrip("_"), None)
    for field in dataclasses.fields(msg):
        key = field.name.rstrip("_")
        value = doc.get(key)
        attr = getattr(msg, field.name)
        if isinstance(attr, betterproto.Message) and isinstance(value, dict):
            if not betterproto.serialized_on_wire(attr):
                del doc[key]
            else:
                _prune(attr, value)
        elif isinstance(attr, list) and isinstance(value, list):
            for item, item_doc in zip(attr, value):
                if isinstance(item, betterproto.Message):
                    _prune(item, item_doc)


def decode(blob: bytes, proto: str, message: str, lib: ProtoLib,
           source: str = "") -> str:
    """Converts a raw protobuf blob to the .pb.yaml form.

    The output has all fields, also the fields with the default value.
    Enum values are names. Bytes values are base64. The output is valid
    input for render.

    Args:
        blob: The serialized message.
        proto: The proto file stem.
        message: The message name.
        lib: The proto library of the run.
        source: The origin of the blob, for a comment line. An empty string
            writes no comment.

    Returns:
        The YAML text.

    Raises:
        BlobError: The proto does not compile, or the blob is not a valid
            message of the type.
    """
    mtype = lib.message_type(proto, message)
    try:
        msg = mtype.cls().parse(blob)
    except Exception as e:
        raise BlobError(f"{source or 'blob'}: not a valid {mtype.name} "
                        f"message: {e}") from e
    data = msg.to_dict(casing=betterproto.Casing.SNAKE,
                       include_default_values=True)
    _prune(msg, data)
    doc = {"proto": proto, "message": message, "data": data}
    text = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
    if source:
        text = f"# Decoded from {source} ({len(blob)} B).\n" + text
    return text
