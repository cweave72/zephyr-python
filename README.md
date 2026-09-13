# python

This repository is one part of the Zephyr workspace. The workspace also
contains the `applications`, `common`, and `proto` repositories. Each
repository has its own git history.

This repository contains the host tools for the workspace. The tools are
Python packages. They send RPC commands to a device, generate code from
`.proto` files, generate new applications, and read device trace data.

The device code for the tools is in `common/modules/`.

Each package directory contains its own README.

## The two modes

You can use this repository in a workspace, or alone.

| Mode       | Proto files                                       | `app_gen`      |
|------------|---------------------------------------------------|----------------|
| Workspace  | The workspace supplies them through `PROTO_BASE`. | Available.     |
| Standalone | The `proto` submodule in this repository.         | Not available. |

The tools read the `.proto` files of the `proto` repository. In a standalone
checkout, a git submodule supplies these files. The build uses the submodule
when `PROTO_BASE` is not set.

`app_gen` needs the full workspace. It reads the module and board data from
the `common` repository. It writes the new application to the `applications`
repository. Thus `app_gen` fails in a standalone checkout. All other tools
operate in both modes.

### Standalone installation

```sh
git clone --recurse-submodules https://github.com/cweave72/zephyr-python
cd zephyr-python
source init_venv.sh
```

If you cloned without `--recurse-submodules`, initialize the submodule:

```sh
git submodule update --init
```

west does not clone submodules. Thus a workspace checkout contains an empty
`proto` directory. This is correct: the workspace supplies the proto files
through `PROTO_BASE`.

## Workspace installation

Set up the workspace environment first. Refer to the workspace README for
that procedure. The workspace must set the `PROTO_BASE` environment variable
to its proto directory.

Then create and activate the venv:

```sh
cd python
source init_venv.sh
```

`init_venv.sh` calls `uv sync`. Then it activates `.venv`. uv installs each
package in this directory as an editable package. uv also puts the CLI
commands of each package in the `PATH` of the venv.

### How to create the venv again

Use `rebuild_venv.sh` to delete the venv and to generate all protobuf
bindings again:

```sh
./rebuild_venv.sh
source init_venv.sh
```

A plain `uv sync` does not generate the bindings again. Refer to
[proto_builder](proto_builder/README.md).

## Generated bindings

Some packages do not contain their Python bindings. These packages generate
the bindings at build time. Each package declares `proto_builder` as its
build backend. `proto_builder` runs `protoc` and writes the bindings to the
`<package>/lib/` directory.

git does not track the bindings. `.gitignore` contains `**/lib`. Thus you
must build a package before you use it. `init_venv.sh` and `rebuild_venv.sh`
both build the packages.

`proto_builder` also writes a registry file to
`~/.local/share/protorpc/registry/`. The CLI tools read this registry. The
registry gives the Python class for each callset ID that a device reports.

## Requirements

- Python 3.10 or a subsequent version. The venv uses Python 3.11.
- The proto files. In a workspace, the `PROTO_BASE` environment variable
  gives their location. In a standalone checkout, the `proto` submodule
  supplies them.
- `uv`. Refer to the workspace README for the installation procedure.
