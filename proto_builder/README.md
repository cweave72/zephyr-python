# proto_builder

proto_builder is a PEP 517 build backend. It generates
[betterproto](https://github.com/danielgtaylor/python-betterproto) Python
bindings from the workspace `.proto` files. It generates the bindings at
install time.

Three packages wrap a device RPC callset: `systemrpc`, `rtosutils`, and
`protorpcheader`. These packages do not commit their bindings to git. Each
package declares proto_builder as its build backend. proto_builder generates
new bindings each time you build or install the package. proto_builder also
writes a registry entry. The `protorpc` CLI reads this entry at run time.

proto_builder is a wrapper around `setuptools.build_meta`. It generates the
bindings first. Then it calls setuptools to build the wheel.

## How to use proto_builder in a package

Add three sections to the `pyproject.toml` file of the package:

```toml
[build-system]
requires = ["setuptools", "proto_builder", "betterproto[compiler]==2.0.0b6"]
build-backend = "proto_builder.backend"

[tool.proto_builder]
name = "systemrpc"
proto_file = "SystemRpc"
proto_mod = "system"
callset_name = "Callset"

[tool.uv.sources]
proto_builder = { path = "../proto_builder" }
```

The `[tool.uv.sources]` section tells uv to get proto_builder from the
workspace. Without this section, uv gets proto_builder from PyPI.
proto_builder is a build dependency only. uv does not install it as an
editable package.

### Configuration keys

Put all keys in the `[tool.proto_builder]` section.

| Key            | Required | Description                                                                                                                |
|----------------|----------|----------------------------------------------------------------------------------------------------------------------------|
| `name`         | yes      | The package name. proto_builder makes the name lowercase. It then uses the name for the output directory and the log file. |
| `proto_file`   | yes      | The name of the proto file. Do not include the `.proto` extension. Example: `SystemRpc`.                                   |
| `proto_mod`    | no       | The module name in the run-time registry.                                                                                  |
| `callset_name` | no       | The callset class name in the run-time registry.                                                                           |

proto_builder writes a registry entry only if you set `proto_mod` and
`callset_name`. `protorpcheader` does not set these keys. `protorpcheader`
supplies the RPC frame header. It does not supply a callset. Thus no package
can use it as an API.

## Requirements

Use Python 3.10 or a subsequent version. proto_builder reads the package
configuration with `tomllib` on Python 3.11 and subsequent versions. On
Python 3.10, proto_builder reads the configuration with the `tomli` backport.
`tomli` is a conditional dependency. Thus a Python 3.10 build environment
installs it automatically.

uv creates each build environment with an interpreter that obeys
`requires-python`. This interpreter can be different from the Python 3.11
interpreter in the tools venv.

proto_builder finds the proto files in one of two locations.

1. The `PROTO_BASE` environment variable. `workspace-env.sh` sets this
   variable in a workspace:

   ```sh
   export PROTO_BASE=$WORKSPACE_BASE/proto
   ```

2. The `proto` submodule of the `zephyr-python` repository. proto_builder uses
   the submodule only if `PROTO_BASE` is not set. This permits a standalone
   checkout with no workspace.

The build fails if neither location supplies a proto file. An uninitialized
submodule is an empty directory. Thus proto_builder looks for a proto file,
not for the directory.

## Build sequence

proto_builder does these steps during `build_wheel` and `build_editable`. It
does these steps before setuptools builds the wheel.

1. Read the `[tool.proto_builder]` section from the `pyproject.toml` file in
   the current directory.
2. Send stdout to `<name>.log`. setuptools discards stdout.
3. Find all `*.proto` files in `$PROTO_BASE` and its subdirectories. Make an
   `-I` include path from the directory of each file. Also include the
   `.proto` files of `grpc_tools`. These files supply `google/protobuf/*`.
4. Find the file that has the `proto_file` name. Fail if the file is not
   there.
5. Delete the `<cwd>/<name>/lib` directory. Create the directory again. Then
   run `protoc --python_betterproto_out=` into the directory.
6. Write the registry entry if you set `proto_mod` and `callset_name`.

Then `setuptools.build_meta` builds the wheel.

### Generated files

proto_builder writes the bindings to the `<name>/lib/` directory in the
package. Example: `systemrpc/systemrpc/lib/`.

betterproto writes one module for each proto package. Thus a `.proto` file
that declares `package system;` generates the `systemrpc/lib/system/`
directory. Import the class as follows:

```python
from systemrpc.lib.system import Callset
```

betterproto writes messages that have no proto package to `lib/__init__.py`.
It also writes imported definitions, such as `nanopb.proto`, to this file.

git does not track the generated files. `python/.gitignore` contains `**/lib`
and `*.log`.

The build controls all files in the `lib/` directory. Each build deletes the
directory and creates it again. Thus modules for deleted protos do not stay
in the directory.

If `protoc` fails, the `lib/` directory contains no bindings. You cannot
import the package until a build completes correctly.

## Run-time registry

proto_builder writes a registry entry to this path:

```
~/.local/share/protorpc/registry/<proto_mod>.yaml
```

```yaml
package: systemrpc.lib
module: system
cls: Callset
```

The `protorpc` CLI reads this file. The CLI uses the file to find the Python
class for a callset ID. The device supplies the callset ID. Refer to the
[protorpc README](../protorpc/README.md).

## Hook behavior

| Hook                               | Behavior                                          |
|------------------------------------|---------------------------------------------------|
| `build_wheel`                      | Generates the bindings. Then calls setuptools.    |
| `build_editable`                   | Generates the bindings. Then calls setuptools.    |
| `build_sdist`                      | Calls setuptools. **Does not generate bindings.** |
| `get_requires_for_build_wheel`     | Calls setuptools.                                 |
| `prepare_metadata_for_build_wheel` | Calls setuptools.                                 |

Thus an sdist contains no bindings. This workspace installs all packages from
source with uv. Thus this condition causes no problem here. But an sdist is
not a complete package.

## Troubleshooting

**Read `<name>.log` first.** proto_builder sends stdout to `<name>.log` during
the full generation step. The log contains the `protoc` command, the list of
proto files, and the text of most failures. The log is in the package
directory. If setuptools shows an unclear error, read the log.

**`PROTO_BASE env variable not set`.** You did not source the workspace
environment in the shell that starts the build.

**`Proto file <X>.proto not found`.** The `proto_file` value does not agree
with a `.proto` file in `$PROTO_BASE`. proto_builder compares the file name
only. The subdirectory is not important. But the spelling must agree exactly.

**Old registry entries.** proto_builder makes the registry file name from
`proto_mod`. If you change `proto_mod`, the old YAML file stays in the
registry directory. proto_builder does not delete it. The registry directory
contains `system.yaml` and an old `systemrpc.yaml` file. Two entries cause no
problem. The CLI compares the `module` field and uses the first entry that
agrees. But delete the old files if the quantity increases.
