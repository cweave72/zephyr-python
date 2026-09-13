# protorpc

protorpc is the Python part of **ProtoRpc**. ProtoRpc is the protobuf RPC
mechanism for the Zephyr devices in this workspace.

The package does two tasks:

1. **Client library.** The library connects to the device RPC server with TCP
   or UDP. It builds a Python API from the generated protobuf callset classes.
   It also does the frame, sequence, and reply operations.
2. **C handler generation.** A `protoc` plugin reads a `.proto` callset file.
   The plugin writes C handler source files for the device.

The device code is in `common/modules/ProtoRpc`.

## Installation

protorpc is part of the `python/` uv workspace. uv installs it as an editable
dependency of `zephyr-python`. Run this command in the `python/` directory:

```sh
uv sync
```

uv puts both console scripts in the `PATH` of the venv. The generator writes
a temporary file to its own installed directory. Thus this directory must
permit write operations. Refer to [Generator internals](#generator-internals).

## Console scripts

| Script                | Description                                                                   |
|-----------------------|-------------------------------------------------------------------------------|
| `run_protorpc_gen`    | A CLI. It starts `protoc` to generate C handler source files.                 |
| `protoc-gen-protorpc` | The `protoc` plugin. Do not run it directly. `protoc` finds it in the `PATH`. |

Use the two scripts together. `run_protorpc_gen` starts `protoc` with the
`--protorpc_out` option. Then `protoc` finds the `protoc-gen-protorpc`
program in the `PATH`. Both scripts start `protorpc.generator.generator`.

### How to generate C handlers

```sh
run_protorpc_gen \
    -i $PROTO_BASE/SystemRpc \
    -i $NANOPB_BASE/generator/proto \
    --outpath=protorpc_build \
    $PROTO_BASE/SystemRpc/SystemRpc.proto
```

| Option            | Description                                                       |
|-------------------|-------------------------------------------------------------------|
| `-i`, `--include` | A proto include path. You can use this option more than one time. |
| `--outpath`       | The output directory. The default is `./protorpc_out`.            |
| `--loglevel`      | The log level of the CLI. The default is `info`.                  |
| `--gen-loglevel`  | The log level of the plugin. The default is `info`.               |
| `--debug`         | Sets `--loglevel` to `debug`.                                     |

For each `Foo.proto` file, the plugin writes a `Foo.c` file and a `Foo.h`
file. The files contain a `Foo_resolver()` function. The files also contain
one `static` handler function for each `*_call` message in the callset
`oneof`. The plugin puts the handlers in a `ProtoRpc_Handler_Entry` table.

Each handler contains a `TODO` comment only. Generate the files one time.
Then write the handler code and keep the files.

> **The generator deletes the output directory at each start.** Set
> `--outpath` to a temporary directory. Do not set it to a directory that
> contains your own source files.

`common/scripts/make/protorpc_handlers.mk` contains the build rules:

```sh
make handlers PROTO_SOURCE_IN=$PROTO_BASE/SystemRpc/SystemRpc.proto
```

## How to use the library

Start with the `build_api()` function. Supply the header class, the callsets,
and the connection data. The function returns the API object and the
connection object.

```python
from protorpc import build_api
from protorpcheader.lib import ProtoRpcHeader
from systemrpc.lib.system import Callset as SystemCallset

api, conn = build_api(
    ProtoRpcHeader,
    callsets=[(SystemCallset, 0, "system")],
    protocol="tcp",        # or "udp"
    port=13001,
    addr="192.168.1.50",   # or hostname="mydevice"
)

reply = api["system"].getinfo()
if reply.success:
    print(reply.result)

conn.close()
```

Supply each callset as a `(callset_cls, id, name)` tuple. The
`parse_callset_fields()` function reads the generated betterproto class. Each
`*_call` message in the `msg` oneof becomes a method of the `Api` object.
The method name does not contain the `_call` text. Example: `getinfo_call`
becomes `getinfo()`.

A call waits for the reply. To send a call without a reply, set
`no_reply=True`.

### Replies

Each call returns a `Reply` object.

| Attribute        | Description                                                                   |
|------------------|-------------------------------------------------------------------------------|
| `success`        | `True` if the device returned `SUCCESS`.                                      |
| `result`         | The decoded reply message.                                                    |
| `timedout`       | `True` if no reply came before the timeout. The default timeout is 3 seconds. |
| `status_str`     | The status as text. Example: `HANDLER_ERROR`.                                 |
| `exit_on_fail()` | Writes an error and calls `sys.exit(1)` if the call failed.                   |

## Callset registry and device bindings

A CLI tool must find the Python class for each callset ID that a device
reports. Two files supply this data.

**The registry.** The registry contains one YAML file for each generated API
package. The files are in `~/.local/share/protorpc/registry/`. Each file
gives the location of the callset class:

```yaml
package: systemrpc.lib
module: system
cls: Callset
```

**The device bindings.** The `.cli_device_callsets` file is in the working
directory. The file uses the device IP address as the key. The CLI sends a
frame that contains the `callset_query` flag. The device replies with its
callset ID, name, and version. The CLI then compares these names with the
registry.

The CLI refreshes the bindings when it finds a new shell session. The
`.cli_session_state` file holds the session data. You can also use the
`--refresh-bindings` option.

Use `--dump-registry` to show the registry. Use `-c` or `--callsets` to
supply a YAML file and to omit the device query. Refer to
`protorpc/cli/callets_example.yaml` for the file format.

The `CallsetBase.check_version()` method compares two versions: the callset
version of the device and the version of the Python API. A different major
version causes an error. A later device minor version also causes an error.
An earlier device minor version causes a warning.

## How to build a CLI

The `systemrpc`, `rtosutils`, and `testrpc` packages do not write their own
connection code. Each package adds the `cli_common_opts` decorator to its
entry point. Each package then calls `cli_init`. The `cli_init` function does
the log setup, the session refresh, the callset import, and the `build_api`
call.

```python
import click
from protorpc.cli import get_params
from protorpc.cli.common_opts import cli_common_opts, cli_init, CONTEXT_SETTINGS

@click.group(context_settings=CONTEXT_SETTINGS)
@cli_common_opts
@click.pass_context
def cli(ctx, **kwargs):
    ctx.ensure_object(dict)
    api, conn, bindings = cli_init(ctx, get_params(**kwargs))
```

`cli_common_opts` supplies these options: `--ip`, `--hostname`, `--port`,
`-u/--udp`, `-c/--callsets`, `--loglevel`, `-d/--debug`, `--dump-registry`,
and `--refresh-bindings`.

## Frame format

A frame contains a `ProtoRpcHeader` message. An optional callset message
comes after the header. A protobuf varint gives the length of each message:

```
[varint len][ProtoRpcHeader][varint len][Callset]
```

The header contains the sequence number, the callset ID, the `no_reply` flag,
and the `callset_query` flag. A reply header also contains the status.

A TCP connection puts the frame in a COBS frame. Refer to
`protorpc/connection/cobs.py`. Thus the `0x00` byte is the frame delimiter. A
UDP connection sends the frame without COBS.

Each connection runs a daemon thread. The thread reads the replies. It
compares the sequence number of each reply with the sequence number of the
request. The thread also controls the timeout.

## Package contents

| Path                   | Description                                                               |
|------------------------|---------------------------------------------------------------------------|
| `protorpc/__init__.py` | The `build_api()` function and the log setup.                             |
| `protorpc/api.py`      | The `Request`, `Reply`, and `Api` classes. Also reads the callset fields. |
| `protorpc/connection/` | The `BaseConnection` thread, the TCP and UDP code, and COBS.              |
| `protorpc/cli/`        | The common CLI options, the registry, and the device bindings.            |
| `protorpc/generator/`  | The `protoc` plugin and the CLI for C handler generation.                 |
| `protorpc/util/`       | The `CallsetBase` class and the version comparison.                       |

## Generator internals

`run_protorpc_gen` and the plugin are two different processes. `protoc`
starts the plugin. Thus `run_protorpc_gen` cannot send the options directly
to the plugin. `run_protorpc_gen` writes the plugin log level and the output
path to a `.genlog` file. The file is in the same directory as
`protorpc/generator/generator.py`. The plugin reads the file when it starts.
Thus this directory must permit write operations. `.genlog` is in the
`.gitignore` file.

Do not run `protoc-gen-protorpc` directly. The `.genlog` file is not there
and the program fails. `protoc` starts this program during a
`run_protorpc_gen` operation.

The plugin writes its log to `<outpath>/protorpc_out.log`.
