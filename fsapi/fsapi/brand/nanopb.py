"""Checks YAML data against a protobuf message and its nanopb options.

The firmware decodes a blob into a nanopb struct. nanopb gives each string,
bytes and repeated field a fixed size from the (nanopb) field options. A
value which is too large makes pb_decode fail on the device. A field with no
size is a pb_callback_t, and pb_decode without a callback drops its data with
no error. This module finds these problems on the host, at build time.

The module reads the message definitions from a protoc descriptor set
(FileDescriptorSet), not from the betterproto bindings, because betterproto
does not keep the custom options.
"""
from __future__ import annotations

import base64
import binascii
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from google.protobuf.descriptor_pb2 import (DescriptorProto,
                                            EnumDescriptorProto,
                                            FieldDescriptorProto,
                                            FileDescriptorProto,
                                            FileDescriptorSet)

F = FieldDescriptorProto

INT_TYPES = {F.TYPE_INT32, F.TYPE_SINT32, F.TYPE_SFIXED32, F.TYPE_UINT32,
             F.TYPE_FIXED32}
# betterproto from_dict also accepts a 64-bit value as a string (JSON rule).
INT64_TYPES = {F.TYPE_INT64, F.TYPE_SINT64, F.TYPE_SFIXED64, F.TYPE_UINT64,
               F.TYPE_FIXED64}
FLOAT_TYPES = {F.TYPE_FLOAT, F.TYPE_DOUBLE}

# The nanopb_pb2 module of this process. Protobuf registers nanopb.proto in
# the default descriptor pool when the module loads. Thus load it one time.
_nanopb_pb2: Optional[ModuleType] = None


def load_nanopb_pb2(py_file: Path) -> ModuleType:
    """Loads the nanopb_pb2 module, which protoc makes from nanopb.proto.

    The module registers the (nanopb), (nanopb_msgopt) and (nanopb_fileopt)
    option extensions. After the load, a FileDescriptorSet parse keeps the
    nanopb option values. Load the module before the parse.

    Args:
        py_file: The nanopb_pb2.py file of protoc --python_out.

    Returns:
        The module. A second call returns the module of the first call.
    """
    global _nanopb_pb2
    if _nanopb_pb2 is None:
        spec = importlib.util.spec_from_file_location("nanopb_pb2", py_file)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["nanopb_pb2"] = module
        spec.loader.exec_module(module)
        _nanopb_pb2 = module
    return _nanopb_pb2


@dataclass
class MessageInfo:
    """The definition of one message type.

    Attributes:
        desc: The message descriptor.
        file: The descriptor of the file which defines the message.
        chain: The descriptors of the enclosing messages, outermost first,
            for the (nanopb_msgopt) options of a nested message.
    """
    desc: DescriptorProto
    file: FileDescriptorProto
    chain: list[DescriptorProto]


class DescriptorIndex:
    """Finds message and enum types by full name in a descriptor set."""

    def __init__(self, fds: FileDescriptorSet, nanopb_pb2: ModuleType) -> None:
        """Indexes all message and enum types of the descriptor set.

        Args:
            fds: The descriptor set of protoc --descriptor_set_out
                --include_imports. Parse it after load_nanopb_pb2.
            nanopb_pb2: The module from load_nanopb_pb2.
        """
        self.nanopb_pb2 = nanopb_pb2
        self.messages: dict[str, MessageInfo] = {}
        self.enums: dict[str, EnumDescriptorProto] = {}
        for file in fds.file:
            prefix = f".{file.package}" if file.package else ""
            for enum in file.enum_type:
                self.enums[f"{prefix}.{enum.name}"] = enum
            for msg in file.message_type:
                self._add_message(file, prefix, msg, [])

    def _add_message(self, file: FileDescriptorProto, prefix: str,
                     msg: DescriptorProto, chain: list[DescriptorProto]) -> None:
        """Adds a message, and its nested messages and enums, to the index.

        Args:
            file: The file which defines the message.
            prefix: The full name of the scope, with a leading dot.
            msg: The message descriptor.
            chain: The enclosing messages, outermost first.
        """
        name = f"{prefix}.{msg.name}"
        self.messages[name] = MessageInfo(msg, file, chain)
        for enum in msg.enum_type:
            self.enums[f"{name}.{enum.name}"] = enum
        for nested in msg.nested_type:
            self._add_message(file, name, nested, chain + [msg])

    def find_message(self, name: str) -> Optional[MessageInfo]:
        """Finds a message type.

        Args:
            name: The full name, with or without the leading dot, for
                example 'netconf.NetConf'.

        Returns:
            The message definition, or None if the set does not have it.
        """
        return self.messages.get(name if name.startswith(".") else f".{name}")

    def message_names(self, package: str) -> list[str]:
        """Lists the message names of one package, without the package.

        Args:
            package: The proto package, for example 'netconf'.

        Returns:
            The sorted names, for example ['Ipv4', 'NetConf'].
        """
        prefix = f".{package}." if package else "."
        return sorted(n[len(prefix):] for n in self.messages
                      if n.startswith(prefix)
                      and not self.messages[n].desc.options.map_entry)

    def field_options(self, info: MessageInfo, field: FieldDescriptorProto) -> Any:
        """Gets the nanopb options of a field.

        nanopb applies the file options, then the options of each enclosing
        message, then the field options. A later value replaces an earlier
        one.

        Args:
            info: The message which holds the field.
            field: The field descriptor.

        Returns:
            A merged NanoPBOptions message.
        """
        pb2 = self.nanopb_pb2
        opts = pb2.NanoPBOptions()
        opts.MergeFrom(info.file.options.Extensions[pb2.nanopb_fileopt])
        for msg in info.chain + [info.desc]:
            opts.MergeFrom(msg.options.Extensions[pb2.nanopb_msgopt])
        opts.MergeFrom(field.options.Extensions[pb2.nanopb])
        return opts


def _has_static_size(opts: Any, attr: str) -> bool:
    """Tests if a field option gives nanopb a fixed size.

    Args:
        opts: The merged NanoPBOptions of the field.
        attr: 'max_size' or 'max_count'.

    Returns:
        True if nanopb allocates the field in the struct or on the heap.
        False if nanopb makes a pb_callback_t.
    """
    pb2 = _nanopb_pb2
    assert pb2 is not None
    if opts.HasField("type"):
        if opts.type == pb2.FT_CALLBACK:
            return False
        if opts.type in (pb2.FT_POINTER, pb2.FT_IGNORE):
            return True
    if attr == "max_size":
        return opts.HasField("max_size") or opts.HasField("max_length")
    return opts.HasField("max_count")


def _callback_error(field: FieldDescriptorProto, opts: Any,
                    path: str) -> Optional[str]:
    """Checks that a string or bytes field has a nanopb size.

    Args:
        field: The field descriptor.
        opts: The merged NanoPBOptions of the field.
        path: The field path, for the error text.

    Returns:
        The error text if nanopb makes a callback field for the value.
        None for a sized field, or for a field of another type.
    """
    if field.type not in (F.TYPE_STRING, F.TYPE_BYTES):
        return None
    if _has_static_size(opts, "max_size"):
        return None
    return (f"{path}: the field has no (nanopb).max_size. nanopb makes a "
            f"callback field, and Pb_unpack drops its data. Set max_size in "
            f"the .proto.")


def _max_size(opts: Any) -> int:
    """Gets the nanopb buffer size of a string or bytes field.

    Args:
        opts: The merged NanoPBOptions of the field.

    Returns:
        The size in bytes. For a string, the size includes the NUL.
        max_length gives max_length + 1.
    """
    if opts.HasField("max_size"):
        return opts.max_size
    return opts.max_length + 1


def check_message(index: DescriptorIndex, info: MessageInfo, data: Any,
                  where: str = "") -> list[str]:
    """Checks YAML data against a message and its nanopb options.

    The function does not stop at the first problem. It returns all
    problems, so the user can correct them in one step.

    Args:
        index: The descriptor index.
        info: The message type of data.
        data: The YAML value of the message. It must be a mapping of field
            names to values.
        where: The field path of data in the root message, for the error
            text. An empty string is the root.

    Returns:
        A list of error texts, for example
        'ipv4.address: 20 B > max_size 16'. An empty list if the data is
        correct.
    """
    at = where or "data"
    if data is None:
        return []
    if not isinstance(data, dict):
        return [f"{at}: must be a mapping of field names to values, not "
                f"{type(data).__name__}"]

    errors: list[str] = []
    fields = {f.name: f for f in info.desc.field}
    oneof_seen: dict[int, str] = {}
    for key, value in data.items():
        path = f"{where}.{key}" if where else str(key)
        field = fields.get(key)
        if field is None:
            errors.append(f"{path}: {info.desc.name} has no field '{key}'. "
                          f"Fields: {', '.join(fields)}")
            continue
        if value is None:
            continue
        if field.HasField("oneof_index") and not field.proto3_optional:
            other = oneof_seen.get(field.oneof_index)
            if other is not None:
                oneof = info.desc.oneof_decl[field.oneof_index].name
                errors.append(f"{path}: '{other}' and '{key}' are in one "
                              f"oneof ({oneof}). Set only one.")
            oneof_seen[field.oneof_index] = key
        errors += _check_field(index, info, field, value, path)
    return errors


def _check_field(index: DescriptorIndex, info: MessageInfo,
                 field: FieldDescriptorProto, value: Any,
                 path: str) -> list[str]:
    """Checks the value of one field.

    Args:
        index: The descriptor index.
        info: The message which holds the field.
        field: The field descriptor.
        value: The YAML value. Not None.
        path: The field path, for the error text.

    Returns:
        A list of error texts. Empty if the value is correct.
    """
    opts = index.field_options(info, field)
    if field.label != F.LABEL_REPEATED:
        return _check_single(index, info, field, opts, value, path)

    entry = (index.find_message(field.type_name)
             if field.type == F.TYPE_MESSAGE else None)
    is_map = entry is not None and entry.desc.options.map_entry
    if is_map and not isinstance(value, dict):
        return [f"{path}: a map field must be a mapping"]
    if not is_map and not isinstance(value, list):
        return [f"{path}: a repeated field must be a list"]
    if not _has_static_size(opts, "max_count"):
        return [f"{path}: the field has no (nanopb).max_count. nanopb makes "
                f"a callback field, and Pb_unpack drops its data. Set "
                f"max_count in the .proto."]

    errors: list[str] = []
    if opts.HasField("max_count") and len(value) > opts.max_count:
        errors.append(f"{path}: {len(value)} items > max_count "
                      f"{opts.max_count}")
    if is_map:
        # nanopb does not copy the options of a map field to the key and
        # value fields of the entry. Thus check the entry fields.
        assert entry is not None
        key_f, value_f = entry.desc.field[0], entry.desc.field[1]
        key_o = index.field_options(entry, key_f)
        value_o = index.field_options(entry, value_f)
        size_errors = [e for e in (_callback_error(key_f, key_o, f"{path} key"),
                                   _callback_error(value_f, value_o,
                                                   f"{path} value")) if e]
        if size_errors:
            return errors + size_errors
        for k, v in value.items():
            errors += _check_single(index, entry, key_f, key_o, k,
                                    f"{path}[{k!r}] key")
            if v is not None:
                errors += _check_single(index, entry, value_f, value_o, v,
                                        f"{path}[{k!r}]")
    else:
        size_error = _callback_error(field, opts, f"{path} items")
        if size_error:
            return errors + [size_error]
        for i, item in enumerate(value):
            if item is None:
                errors.append(f"{path}[{i}]: an item cannot be empty")
                continue
            errors += _check_single(index, info, field, opts, item,
                                    f"{path}[{i}]")
    return errors


def _check_single(index: DescriptorIndex, info: MessageInfo,
                  field: FieldDescriptorProto, opts: Any, value: Any,
                  path: str) -> list[str]:
    """Checks one value of a field: a singular value or one list item.

    Args:
        index: The descriptor index.
        info: The message which holds the field.
        field: The field descriptor.
        opts: The merged NanoPBOptions of the field.
        value: The YAML value. Not None.
        path: The field path, for the error text.

    Returns:
        A list of error texts. Empty if the value is correct.
    """
    t = field.type
    if t == F.TYPE_MESSAGE:
        sub = index.find_message(field.type_name)
        if sub is None:
            return [f"{path}: type {field.type_name} is not in the "
                    f"descriptor set"]
        return check_message(index, sub, value, path)

    if t == F.TYPE_ENUM:
        enum = index.enums.get(field.type_name)
        names = [v.name for v in enum.value] if enum else []
        if not isinstance(value, str) or value not in names:
            enum_name = field.type_name.rsplit(".", 1)[-1]
            return [f"{path}: {value!r} is not a value of enum {enum_name} "
                    f"({', '.join(names)})"]
        return []

    if t == F.TYPE_STRING:
        if not isinstance(value, str):
            return [f"{path}: must be a string, not {type(value).__name__}. "
                    f"Put quotes around the value."]
        size_error = _callback_error(field, opts, path)
        if size_error:
            return [size_error]
        if opts.HasField("max_size") or opts.HasField("max_length"):
            size = len(value.encode("utf-8")) + 1
            limit = _max_size(opts)
            if size > limit:
                return [f"{path}: {size} B (with the NUL) > max_size {limit}"]
        return []

    if t == F.TYPE_BYTES:
        if not isinstance(value, str):
            return [f"{path}: a bytes field must be a base64 string"]
        try:
            raw = base64.b64decode(value, validate=True)
        except binascii.Error:
            return [f"{path}: {value!r} is not base64"]
        size_error = _callback_error(field, opts, path)
        if size_error:
            return [size_error]
        if opts.HasField("max_size") and len(raw) > opts.max_size:
            return [f"{path}: {len(raw)} B > max_size {opts.max_size}"]
        return []

    if t == F.TYPE_BOOL:
        if not isinstance(value, bool):
            return [f"{path}: must be true or false"]
        return []
    if t in INT_TYPES or t in INT64_TYPES:
        ok = isinstance(value, int) and not isinstance(value, bool)
        if t in INT64_TYPES and isinstance(value, str):
            ok = value.lstrip("-").isdigit()
        if not ok:
            return [f"{path}: must be an integer, not {value!r}"]
        return []
    if t in FLOAT_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{path}: must be a number, not {value!r}"]
        return []
    return []
