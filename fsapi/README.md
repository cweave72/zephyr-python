# fsapi

fsapi is the host client for the **FsApiRpc** callset. It gives remote access
to the littlefs file systems of a Zephyr device, for example `/flash` and
`/ram`. The package has two parts:

1. **`fsapi-cli`**. A command line tool: list, copy, move, remove and format.
2. **`FsApi` class**. A Python API for scripts and tests.
3. **`fsapi-brand`**. Builds a littlefs image of a directory for branding.
   It converts `.pb.yaml` files to protobuf blobs.

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
`/flash/logs/a.txt`. `fsapi-cli mounts` lists the mount points.

```sh
fsapi-cli --ip <device-ip> <command> [ARGS]
```

| Command                                      | Description                                                                                 |
|----------------------------------------------|---------------------------------------------------------------------------------------------|
| `mounts`                                     | Lists the mount points.                                                                     |
| `df [PATH]`                                  | Shows the size, used and free space of each mount, or of the mount which holds PATH.        |
| `ls PATH`                                    | Lists a directory: type, size and name.                                                     |
| `tree PATH`                                  | Shows a directory and all its subdirectories as a tree.                                     |
| `stat PATH`                                  | Shows the type and size of a path.                                                          |
| `cat PATH`                                   | Writes a device file to stdout.                                                             |
| `get PATH DEST`                              | Copies the device file PATH to the local file DEST.                                         |
| `put SRC PATH`                               | Copies the local file SRC to the device file PATH.                                          |
| `rm PATH`                                    | Removes a file or an empty directory.                                                       |
| `mv SRC DST`                                 | Renames or moves a file or directory on one mount.                                          |
| `mkdir PATH`                                 | Creates a directory.                                                                        |
| `closeall`                                   | Closes all file and directory handles on the device.                                        |
| `format MOUNT [--yes]`                       | Erases the mount MOUNT, for example `/ram`. Asks for confirmation without `--yes`.          |
| `pbput YAML PATH`                            | Writes a `.pb.yaml` file as a protobuf blob to PATH. See [Protobuf blobs](#protobuf-blobs). |
| `pbget PATH --proto P --message M [-o FILE]` | Writes the blob PATH in the `.pb.yaml` form to stdout, or to FILE.                          |

The common ProtoRpc options apply, for example `--ip`, `--port` (default
13001) and `--refresh-bindings`. Run `fsapi-cli --help` for the full list.

A file system error prints the device errno name and exits with status 1:

```
$ fsapi-cli --ip 192.168.1.15 rm /flash/p
error: remove /flash/p: ENOTEMPTY (-90)
```

### Examples

```
$ fsapi-cli --ip 192.168.1.15 mounts
/flash
/ram

$ fsapi-cli --ip 192.168.1.15 df
/flash: 32 blocks of 4096 B; size 131072 B; used 65536 B; free 65536 B
/ram: 64 blocks of 512 B; size 32768 B; used 1024 B; free 31744 B

$ fsapi-cli --ip 192.168.1.15 put random1k.dat /ram/r1k.dat
random1k.dat -> /ram/r1k.dat: 1024 B

$ fsapi-cli --ip 192.168.1.15 ls /flash/t
Type  Size  Name
file   128  r128.dat
file  1024  r1k.dat

$ fsapi-cli --ip 192.168.1.15 tree /ram
/ram
└── r1k.dat (1024 B)

0 directories, 1 files

$ fsapi-cli --ip 192.168.1.15 format /nope
error: /nope is not a mount point. Mounts: /flash, /ram
```

A rename between two mounts fails with `EINVAL`. Copy the file with `get` and
`put`, then remove it.

In a new shell, the first command asks the device for its callsets. It prints
the callset table before its own output. Thus do not pipe the first `cat`
command in a new shell to a file. Run `fsapi-cli --ip <device-ip>
--refresh-bindings` first.

## fsapi-brand

`fsapi-brand` builds a littlefs image of a directory tree for a flash mount of
an application build. `make brand` runs it and flashes the image. See
"Branding" in `common/modules/FsApi/README.md`.

```sh
fsapi-brand --build applications/fs_demo/build \
            --src applications/fs_demo/brand/default
```

| Option             | Description                                                                                      |
|--------------------|--------------------------------------------------------------------------------------------------|
| `--build DIR`      | The application build directory. It holds `fsapi_layout.json`.                                   |
| `--src DIR`        | The directory tree. It maps to the mount root. Dot files are skipped.                            |
| `--mount MOUNT`    | The flash mount, for example `/flash`. Required for more mounts.                                 |
| `--out DIR`        | The output directory. The default is `<build>/brand`.                                            |
| `--proto-path DIR` | A proto directory for the `.pb.yaml` files, after `$PROTO_BASE`. Repeat it for more directories. |

Output:

```
Brand image for /flash: 131072 B at 0x101e0000 (partition offset 0x1e0000), 6 of 32 blocks used.
  /flash/etc/config/net.pb  (44 B)  <- etc/config/net.pb.yaml
Wrote applications/fs_demo/build/brand/brand.bin, brand.hex, brand.json.
```

The tool copies the source tree to `<out>/stage`, converts each `.pb.yaml`
file, and builds the image from the stage directory. It uses
`littlefs-python` and pins the littlefs on-disk format to 2.1, the format of
the device littlefs. After it writes the image, it mounts the image again and
compares each file.

## Protobuf blobs

A `<name>.pb.yaml` file in the brand tree describes one protobuf message.
`fsapi-brand` writes the message as the raw protobuf file `<name>.pb` in the
same directory. The `.pb.yaml` file is not in the image. The firmware reads
the blob with `FsApi_unpack_file` into the nanopb struct of the same
`.proto` (see `common/modules/FsApi/README.md`).

```yaml
# brand/default/etc/config/net.pb.yaml -> /flash/etc/config/net.pb
proto: NetConf           # the proto file stem
message: NetConf         # the message name in the proto package
# out: net.pb            # optional; default: the file name minus .yaml
data:
  ipv4:
    mode: IPV4_MODE_STATIC
    address: 192.168.1.16
    netmask: 255.255.255.0
    gateway: 192.168.1.1
```

Rules:

- `data` keys are the proto field names.
- An enum field takes a value name of the `.proto` enum, for example
  `IPV4_MODE_DHCP`. Numbers are not accepted.
- A `bytes` field takes a base64 string.
- A nested message is `Outer.Inner`. The package prefix is optional.
- `out` is a path relative to the directory of the `.pb.yaml` file.
- A `.pb.yaml` file and a plain file which make the same path give an error.

**Proto search path.** The tool finds `<proto>.proto` in `$PROTO_BASE` and
in each `--proto-path` directory. `fsapi-cli` needs `--proto-path` only for a
proto outside `$PROTO_BASE`, for example an application `proto/` directory. `make brandimage` adds the workspace
`proto/` directory and the application `proto/` directory. The same `.proto`
makes the nanopb struct in the firmware (`nanopb_build_sources`), so the two
sides cannot differ. A stem which is in more than one directory is an error.

**Bindings.** The tool compiles the proto at each run: betterproto bindings
for the encoding, and a protoc descriptor set for the checks. The generated
code is in `<out>/proto`. It needs no `uv sync`.

**Checks.** The firmware decodes the blob into a struct of fixed size. A
value which does not fit makes `pb_decode` fail on the device, and a field
with no nanopb size becomes a `pb_callback_t`, which `Pb_unpack` drops with
no error. Thus the tool checks the data against the inline `[(nanopb)...]`
options and stops with exit status 1. It shows all errors of a file at one
time:

| Check                                         | Example error                                                                          |
|-----------------------------------------------|----------------------------------------------------------------------------------------|
| Field name exists                             | `ipv4.adress: Ipv4 has no field 'adress'. Fields: mode, address, netmask, gateway`     |
| Enum value name exists                        | `ipv4.mode: 'DHCP' is not a value of enum Ipv4Mode (IPV4_MODE_STATIC, IPV4_MODE_DHCP)` |
| String size, with the NUL                     | `ipv4.address: 18 B (with the NUL) > max_size 16`                                      |
| Bytes size                                    | `raw: 3 B > max_size 2`                                                                |
| Repeated and map count                        | `nums: 3 items > max_count 2`                                                          |
| String, bytes and repeated fields have a size | `names items: the field has no (nanopb).max_size. nanopb makes a callback field, ...`  |
| One member of a oneof                         | `b: 'a' and 'b' are in one oneof (pick). Set only one.`                                |
| Value type: string, integer, number, bool     | `flag: must be true or false`                                                          |

The tool does not read nanopb `.options` files. It writes a warning if a
`.options` file is next to the proto.

### Update a blob on a running device

`pbget` reads a blob and writes the `.pb.yaml` form. `pbput` does the same
checks as `fsapi-brand`, writes `PATH.tmp` and renames it to `PATH`. A
littlefs rename is atomic, so a power loss keeps the old blob or the new
blob. The firmware reads the blob at boot. Reset the device to apply it.

```sh
fsapi-cli --ip 192.168.1.16 pbget /flash/etc/config/net.pb \
    --proto NetConf --message NetConf -o net.pb.yaml
# Edit net.pb.yaml.
fsapi-cli --ip 192.168.1.16 pbput net.pb.yaml /flash/etc/config/net.pb
```

The `pbget` output has all fields, also the fields with the default value,
so you can see each field to edit. It does not write a submessage which is
not in the blob, or a oneof member which is not set. Thus `pbput` of the
output makes the same blob. A blob which is not a valid message gives an
error:

```
error: /flash/etc/config/net.pb: not a valid NetConf message: index out of range
```

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

print(fs.mounts())                 # ['/flash', '/ram']
fs.put_file("/ram/data.bin", b"\x00" * 5000)
data = fs.get_file("/ram/data.bin")

fd = fs.open("/ram/data.bin", OpenFlags.OPEN_READ)
chunk = fs.read(fd, 100, offset=4000)
fs.close(fd)

try:
    fs.rm("/flash/missing")
except FsApiException as e:
    assert e.result == -DeviceErrno.ENOENT

conn.close()
```

| Method                                    | Description                                          |
|-------------------------------------------|------------------------------------------------------|
| `mounts()`                                | The list of mount points.                            |
| `info(path)`                              | The GetFsInfo reply of the mount which holds path.   |
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
| `format(mount_point)`                     | Erases one mount. All its data is lost.              |

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

The `FsApi.version` attribute (0.3.0) must match the device callset version.
The CLI checks the version at startup. It stops if the major versions differ,
or if the device minor version is newer.
