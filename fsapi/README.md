# fsapi

fsapi is the host client for the **FsApiRpc** callset. It gives remote access
to the littlefs file system of a Zephyr device. The package has two parts:

1. **`fsapi-cli`**. A command line tool: list, copy, move, remove and format.
2. **`FsApi` class**. A Python API for scripts and tests.

The device code is in `common/modules/FsApi`. The callset is defined in
`proto/FsApiRpc/FsApiRpc.proto`. `applications/fs_demo` is an example
device application.

## Installation

fsapi is part of the `python/` uv workspace. The `proto_builder` backend
generates the protobuf bindings into `fsapi/lib` at install time. Set
`PROTO_BASE` first (the workspace environment sets it). Run these commands in
the `python/` directory:

```sh
source ../workspace-env.sh
uv sync --reinstall-package fsapi
```

A plain `uv sync` does not generate the bindings again. After a change to
`FsApiRpc.proto`, use `--reinstall-package fsapi`.

## fsapi-cli

All paths on the device are absolute and include the mount point, for example
`/lfs/logs/a.txt`.

```sh
fsapi-cli --ip <device-ip> <command> [ARGS]
```

| Command          | Description                                                    |
|------------------|----------------------------------------------------------------|
| `df`             | Shows the size, used space and free space of the file system.  |
| `ls PATH`        | Lists a directory: type, size and name.                        |
| `tree PATH`      | Shows a directory and all its subdirectories as a tree.        |
| `stat PATH`      | Shows the type and size of a path.                             |
| `cat PATH`       | Writes a device file to stdout.                                |
| `get PATH DEST`  | Copies the device file PATH to the local file DEST.            |
| `put SRC PATH`   | Copies the local file SRC to the device file PATH.             |
| `rm PATH`        | Removes a file or an empty directory.                          |
| `mv SRC DST`     | Renames or moves a file or directory.                          |
| `mkdir PATH`     | Creates a directory.                                           |
| `closeall`       | Closes all file and directory handles on the device.           |
| `format [--yes]` | Erases the file system. Asks for confirmation without `--yes`. |

The common ProtoRpc options apply, for example `--ip`, `--port` (default
13001) and `--refresh-bindings`. Run `fsapi-cli --help` for the full list.

A file system error prints the device errno name and exits with status 1:

```
$ fsapi-cli --ip 192.168.1.15 rm /lfs/p
error: remove /lfs/p: ENOTEMPTY (-90)
```

### Examples

```
$ fsapi-cli --ip 192.168.1.15 df
/lfs: 32 blocks of 4096 B; size 131072 B; used 28672 B; free 102400 B

$ fsapi-cli --ip 192.168.1.15 put random1k.dat /lfs/t/r1k.dat
random1k.dat -> /lfs/t/r1k.dat: 1024 B

$ fsapi-cli --ip 192.168.1.15 ls /lfs/t
Type  Size  Name
file   128  r128.dat
file  1024  r1k.dat

$ fsapi-cli --ip 192.168.1.15 tree /lfs
/lfs
├── bootcount (4 B)
└── t/
    ├── r128.dat (128 B)
    └── r1k.dat (1024 B)

1 directories, 3 files
```

In a new shell, the first command asks the device for its callsets. It prints
the callset table before its own output. Thus do not pipe the first `cat`
command in a new shell to a file. Run `fsapi-cli --ip <device-ip>
--refresh-bindings` first.

## FsApi class

```python
from protorpc import build_api
from protorpc.cli.callsets import get_callset_bindings, get_callsets
from protorpcheader.lib import ProtoRpcHeader
from fsapi import FsApi, FsApiException, OpenFlags, DeviceErrno

ip = "192.168.1.15"
callsets = get_callsets(get_callset_bindings(ip))
api, conn = build_api(ProtoRpcHeader, callsets, port=13001, addr=ip)
fs = FsApi(api)

fs.put_file("/lfs/data.bin", b"\x00" * 5000)
data = fs.get_file("/lfs/data.bin")

fd = fs.open("/lfs/data.bin", OpenFlags.OPEN_READ)
chunk = fs.read(fd, 100, offset=4000)
fs.close(fd)

try:
    fs.rm("/lfs/missing")
except FsApiException as e:
    assert e.result == -DeviceErrno.ENOENT

conn.close()
```

| Method                                    | Description                                          |
|-------------------------------------------|------------------------------------------------------|
| `info()`                                  | The GetFsInfo reply: mount point and block counts.   |
| `stat(path)`, `exists(path)`              | Type and size of a path. `exists` returns a bool.    |
| `ls(path)`                                | All entries of a directory. Reads all pages.         |
| `ls_table(path)`, `tree(path)`            | A rich table, and a rich tree with counts.           |
| `open(path, flags)`                       | Opens a file. Returns the handle.                    |
| `close(fd)`, `close_all()`                | Closes one handle, or all handles on the device.     |
| `read(fd, size, offset=None)`             | Reads up to 1024 bytes.                              |
| `write(fd, data, offset=None)`            | Writes up to 1024 bytes. Returns the number written. |
| `seek(fd, offset, whence)`, `size(fd)`    | File position and file size.                         |
| `get_file(path)`, `put_file(path, data)`  | Transfers a full file in 1024-byte chunks.           |
| `rm(path)`, `mv(src, dst)`, `mkdir(path)` | Path operations.                                     |
| `format()`                                | Erases the file system. All data is lost.            |

Rules:

- A negative device `result` raises `FsApiException`. `e.result` holds the
  value.
- Decode `e.result` with `DeviceErrno`, not with the Python `errno` module.
  The device values are the Zephyr libc values. Some are not the same as the
  Linux values, for example `ENOTEMPTY` is 90 on the device and 39 on Linux.
- An RPC failure (timeout, bad status) raises `ProtoRpcException`.
- `open` flags are an OR of `OpenFlags` bits: `OPEN_READ`, `OPEN_WRITE`,
  `OPEN_CREATE`, `OPEN_APPEND`, `OPEN_TRUNC`.
- The device has 4 file handles by default. A 5th open gives `EMFILE`.
- `put_file` replaces an existing file.

## Version

The `FsApi.version` attribute (0.2.0) must match the device callset version.
The CLI checks the version at startup. It stops if the major versions differ,
or if the device minor version is newer.
