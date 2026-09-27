import logging
from enum import IntEnum

from rich.table import Table
from rich.tree import Tree

from protorpc.util import CallsetBase, ProtoRpcException

from fsapi.lib.fsapi import EntryType, OpenFlags, Whence

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

# Maximum data size of one Read or Write call (FsApiRpc.proto max_size).
MAX_CHUNK_SIZE = 1024


class DeviceErrno(IntEnum):
    """Device errno values (Zephyr libc). They are not the host values: for
    example, ENOTEMPTY is 90 on the device and 39 on Linux. Decode 'result'
    with this enum, not with the Python errno module.
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
    """A file system call returned a negative errno value."""

    def __init__(self, call, path, result):
        self.result = result
        try:
            name = DeviceErrno(-result).name
        except ValueError:
            name = 'errno'
        where = f" {path}" if path is not None else ""
        super().__init__(f"{call}{where}: {name} ({result})")


class FsApi(CallsetBase):
    """Class which provides access to the FsApiRpc callset.
    """
    name = "fsapi"
    version = "0.2.0"

    def __init__(self, api):
        super().__init__(api)

    def _call(self, func, **kwargs):
        """Sends one call. Raises on an RPC error or a negative result.
        """
        reply = func(**kwargs)
        self.check_reply(reply)
        result = reply.result
        if result.result < 0:
            where = kwargs.get('path', kwargs.get('src', kwargs.get('fd')))
            raise FsApiException(func.__name__, where, result.result)
        return result

    def info(self):
        """Returns the GetFsInfo reply.
        """
        return self._call(self.api.getfsinfo)

    def stat(self, path):
        """Returns the FileInfo of a path.
        """
        return self._call(self.api.stat, path=path).info

    def exists(self, path):
        try:
            self.stat(path)
        except FsApiException as e:
            if e.result == -DeviceErrno.ENOENT:
                return False
            raise
        return True

    def ls(self, path):
        """Returns all FileInfo entries of a directory.
        """
        entries = []
        total = None
        while total is None or len(entries) < total:
            result = self._call(self.api.listdir, path=path,
                                start_idx=len(entries))
            total = result.total
            if not result.entries:
                break
            entries += result.entries
        return entries

    def ls_table(self, path) -> Table:
        """Returns a borderless table of a directory, sorted by name.
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

    def tree(self, path) -> tuple:
        """Returns a tree of a directory and all its subdirectories.
        Returns (tree, num_dirs, num_files). Entries are sorted by name.
        """
        root = Tree(f"[bold magenta]{path}[/]")
        counts = [0, 0]

        def add(node, dir_path):
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

    def open(self, path, flags):
        """Opens a file. flags is an OR of OpenFlags. Returns the handle.
        """
        return self._call(self.api.open, path=path, flags=flags).result

    def close(self, fd):
        return self._call(self.api.close, fd=fd).result

    def close_all(self):
        """Closes all handles on the device. Returns the number closed.
        """
        return self._call(self.api.closeall).result

    def read(self, fd, size, offset=None):
        size = min(size, MAX_CHUNK_SIZE)
        kwargs = dict(fd=fd, size=size)
        if offset is not None:
            kwargs.update(use_offset=True, offset=offset)
        return self._call(self.api.read, **kwargs).data

    def write(self, fd, data, offset=None):
        kwargs = dict(fd=fd, data=bytes(data[:MAX_CHUNK_SIZE]))
        if offset is not None:
            kwargs.update(use_offset=True, offset=offset)
        return self._call(self.api.write, **kwargs).result

    def seek(self, fd, offset, whence=Whence.SEEK_SET):
        return self._call(self.api.seek, fd=fd, offset=offset,
                          whence=whence).result

    def size(self, fd):
        return self._call(self.api.size, fd=fd).result

    def rm(self, path):
        return self._call(self.api.remove, path=path).result

    def mv(self, src, dst):
        return self._call(self.api.rename, src=src, dst=dst).result

    def mkdir(self, path):
        return self._call(self.api.mkdir, path=path).result

    def format(self):
        """Formats the device file system and mounts it again. All data is
        lost. All open handles on the device are closed.
        """
        return self._call(self.api.format).result

    def get_file(self, path) -> bytes:
        """Reads a whole file from the device.
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

    def put_file(self, path, data: bytes):
        """Writes a whole file to the device. Replaces an existing file.
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
