"""Host client for the FsApiRpc callset.

The FsApi class gives access to the littlefs file systems of a Zephyr device
over ProtoRpc. The device code is in common/modules/FsApi.
"""
from __future__ import annotations

import logging
from enum import IntEnum
from typing import Any, Callable, Mapping, Optional

from rich.table import Table
from rich.tree import Tree

from protorpc.util import CallsetBase, ProtoRpcException

from fsapi.lib.fsapi import (EntryType, FileInfo, GetFsInfoReply, OpenFlags,
                             Whence)

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

# Maximum data size of one Read or Write call (FsApiRpc.proto max_size).
MAX_CHUNK_SIZE = 1024


class DeviceErrno(IntEnum):
    """Device errno values (Zephyr libc).

    The device values are not the host values. For example, ENOTEMPTY is 90
    on the device and 39 on Linux. Decode a 'result' value with this enum,
    not with the Python errno module.
    """
    EPERM = 1
    ENOENT = 2
    EIO = 5
    EBADF = 9
    EAGAIN = 11
    ENOMEM = 12
    EACCES = 13
    EBUSY = 16
    EEXIST = 17
    EXDEV = 18
    ENODEV = 19
    ENOTDIR = 20
    EISDIR = 21
    EINVAL = 22
    ENFILE = 23
    EMFILE = 24
    EFBIG = 27
    ENOSPC = 28
    ESPIPE = 29
    EROFS = 30
    ENOSYS = 88
    ENOTEMPTY = 90
    ENAMETOOLONG = 91
    ENOTSUP = 134


class FsApiException(ProtoRpcException):
    """A file system call returned a negative errno value.

    Attributes:
        result: The negative device errno value.
    """

    def __init__(self, call: str, path: Any, result: int) -> None:
        """Makes the exception message from the call, path and errno.

        Args:
            call: The name of the RPC call, for example 'remove'.
            path: The path or handle of the call. None omits it from the
                message.
            result: The negative device errno value.
        """
        self.result = result
        try:
            name = DeviceErrno(-result).name
        except ValueError:
            name = 'errno'
        where = f" {path}" if path is not None else ""
        super().__init__(f"{call}{where}: {name} ({result})")


class FsApi(CallsetBase):
    """Access to the FsApiRpc callset of one device.

    Each method sends one or more RPC calls. A negative device result raises
    FsApiException. An RPC failure raises ProtoRpcException.
    """
    name = "fsapi"
    version = "0.3.0"

    def __init__(self, api: Mapping[str, Any]) -> None:
        """Selects the fsapi callset from the API.

        Args:
            api: The API object of protorpc.build_api, indexed by callset
                name.
        """
        super().__init__(api)

    def _call(self, func: Callable[..., Any], **kwargs: Any) -> Any:
        """Sends one call and checks the reply.

        Args:
            func: The callset function, for example self.api.remove.
            **kwargs: The fields of the call message.

        Returns:
            The reply message of the call.

        Raises:
            ProtoRpcException: The RPC failed.
            FsApiException: The reply 'result' field is negative.
        """
        reply = func(**kwargs)
        self.check_reply(reply)
        result = reply.result
        if result.result < 0:
            where = kwargs.get('path', kwargs.get('src', kwargs.get('fd')))
            raise FsApiException(func.__name__, where, result.result)
        return result

    def mounts(self) -> list[str]:
        """Gets the mount points.

        Returns:
            The mount points, for example ['/flash', '/ram'].
        """
        return list(self._call(self.api.listmounts).mount_points)

    def info(self, path: str) -> GetFsInfoReply:
        """Gets the size and usage of the file system which holds path.

        Args:
            path: A mount point or a path on that mount.

        Returns:
            The GetFsInfo reply: mount point, block size, total and free
            blocks.
        """
        return self._call(self.api.getfsinfo, path=path)

    def stat(self, path: str) -> FileInfo:
        """Gets the type and size of a path.

        Args:
            path: The absolute device path.

        Returns:
            The FileInfo of the path.
        """
        return self._call(self.api.stat, path=path).info

    def exists(self, path: str) -> bool:
        """Tests if a path exists.

        Args:
            path: The absolute device path.

        Returns:
            True if the path exists, False on ENOENT.

        Raises:
            FsApiException: The stat call failed with an errno other than
                ENOENT.
        """
        try:
            self.stat(path)
        except FsApiException as e:
            if e.result == -DeviceErrno.ENOENT:
                return False
            raise
        return True

    def ls(self, path: str) -> list[FileInfo]:
        """Gets all entries of a directory. Reads all reply pages.

        Args:
            path: The absolute device path of the directory.

        Returns:
            The FileInfo of each entry, in device order.
        """
        entries: list[FileInfo] = []
        total = None
        while total is None or len(entries) < total:
            result = self._call(self.api.listdir, path=path,
                                start_idx=len(entries))
            total = result.total
            if not result.entries:
                break
            entries += result.entries
        return entries

    def ls_table(self, path: str) -> Table:
        """Makes a borderless table of a directory, sorted by name.

        Args:
            path: The absolute device path of the directory.

        Returns:
            A rich table with the columns Type, Size and Name.
        """
        table = Table(box=None, pad_edge=False)
        table.add_column('Type', style='magenta')
        table.add_column('Size', justify='right')
        table.add_column('Name', style='yellow')

        for entry in sorted(self.ls(path), key=lambda e: e.name):
            is_dir = entry.type == EntryType.ENTRY_DIR
            table.add_row('dir' if is_dir else 'file',
                          '' if is_dir else str(entry.size),
                          entry.name + ('/' if is_dir else ''))
        return table

    def tree(self, path: str) -> tuple[Tree, int, int]:
        """Makes a tree of a directory and all its subdirectories.

        Entries are sorted by name.

        Args:
            path: The absolute device path of the directory.

        Returns:
            A tuple (tree, number of directories, number of files).
        """
        root = Tree(f"[bold magenta]{path}[/]")
        counts = [0, 0]

        def add(node: Tree, dir_path: str) -> None:
            """Adds the entries of dir_path to node, recursively.

            Args:
                node: The tree node of dir_path.
                dir_path: The absolute device path of the directory.
            """
            for entry in sorted(self.ls(dir_path), key=lambda e: e.name):
                if entry.type == EntryType.ENTRY_DIR:
                    counts[0] += 1
                    child = node.add(f"[bold magenta]{entry.name}/[/]")
                    add(child, f"{dir_path.rstrip('/')}/{entry.name}")
                else:
                    counts[1] += 1
                    node.add(f"[yellow]{entry.name}[/] [dim]({entry.size} B)[/]")

        add(root, path)
        return root, counts[0], counts[1]

    def open(self, path: str, flags: OpenFlags | int) -> int:
        """Opens a file.

        Args:
            path: The absolute device path of the file.
            flags: An OR of OpenFlags values.

        Returns:
            The file handle.
        """
        return self._call(self.api.open, path=path, flags=flags).result

    def close(self, fd: int) -> int:
        """Closes a file handle.

        Args:
            fd: The file handle.

        Returns:
            The device result (0).
        """
        return self._call(self.api.close, fd=fd).result

    def close_all(self) -> int:
        """Closes all file and directory handles on the device.

        Returns:
            The number of handles closed.
        """
        return self._call(self.api.closeall).result

    def read(self, fd: int, size: int, offset: Optional[int] = None) -> bytes:
        """Reads from a file.

        Args:
            fd: The file handle.
            size: The number of bytes to read. The maximum is
                MAX_CHUNK_SIZE; a larger value is reduced.
            offset: The file offset. None reads at the current position.

        Returns:
            The data. It is shorter than size at the end of the file.
        """
        size = min(size, MAX_CHUNK_SIZE)
        kwargs: dict[str, Any] = dict(fd=fd, size=size)
        if offset is not None:
            kwargs.update(use_offset=True, offset=offset)
        return self._call(self.api.read, **kwargs).data

    def write(self, fd: int, data: bytes, offset: Optional[int] = None) -> int:
        """Writes to a file.

        Args:
            fd: The file handle.
            data: The data. Only the first MAX_CHUNK_SIZE bytes are written.
            offset: The file offset. None writes at the current position.

        Returns:
            The number of bytes written.
        """
        kwargs: dict[str, Any] = dict(fd=fd, data=bytes(data[:MAX_CHUNK_SIZE]))
        if offset is not None:
            kwargs.update(use_offset=True, offset=offset)
        return self._call(self.api.write, **kwargs).result

    def seek(self, fd: int, offset: int,
             whence: Whence | int = Whence.SEEK_SET) -> int:
        """Sets the file position.

        Args:
            fd: The file handle.
            offset: The offset, relative to whence.
            whence: The Whence reference point.

        Returns:
            The device result (0).
        """
        return self._call(self.api.seek, fd=fd, offset=offset,
                          whence=whence).result

    def size(self, fd: int) -> int:
        """Gets the size of an open file.

        Args:
            fd: The file handle.

        Returns:
            The file size in bytes.
        """
        return self._call(self.api.size, fd=fd).result

    def rm(self, path: str) -> int:
        """Removes a file or an empty directory.

        Args:
            path: The absolute device path.

        Returns:
            The device result (0).
        """
        return self._call(self.api.remove, path=path).result

    def mv(self, src: str, dst: str) -> int:
        """Renames or moves a file or directory on one mount.

        Args:
            src: The absolute device path of the source.
            dst: The absolute device path of the destination.

        Returns:
            The device result (0).
        """
        return self._call(self.api.rename, src=src, dst=dst).result

    def mkdir(self, path: str) -> int:
        """Creates a directory.

        Args:
            path: The absolute device path.

        Returns:
            The device result (0).
        """
        return self._call(self.api.mkdir, path=path).result

    def format(self, mount_point: str) -> int:
        """Formats one device file system and mounts it again.

        All data on the file system is lost. The open handles on that file
        system are closed.

        Args:
            mount_point: The mount point, for example '/ram'.

        Returns:
            The device result (0).
        """
        return self._call(self.api.format, path=mount_point).result

    def get_file(self, path: str) -> bytes:
        """Reads a whole file from the device.

        Args:
            path: The absolute device path of the file.

        Returns:
            The file content.
        """
        fd = self.open(path, OpenFlags.OPEN_READ)
        data = bytearray()
        try:
            while True:
                chunk = self.read(fd, MAX_CHUNK_SIZE)
                data += chunk
                if len(chunk) < MAX_CHUNK_SIZE:
                    break
        finally:
            self.close(fd)
        return bytes(data)

    def put_file(self, path: str, data: bytes) -> None:
        """Writes a whole file to the device. Replaces an existing file.

        Args:
            path: The absolute device path of the file.
            data: The file content.

        Raises:
            ProtoRpcException: The device wrote fewer bytes than sent.
        """
        flags = OpenFlags.OPEN_WRITE | OpenFlags.OPEN_CREATE | OpenFlags.OPEN_TRUNC
        fd = self.open(path, flags)
        try:
            for offset in range(0, len(data), MAX_CHUNK_SIZE):
                chunk = data[offset:offset + MAX_CHUNK_SIZE]
                num = self.write(fd, chunk)
                if num != len(chunk):
                    raise ProtoRpcException(
                        f"write {path}: short write {num}/{len(chunk)} "
                        f"at offset {offset}")
        finally:
            self.close(fd)
