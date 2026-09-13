# api_generator

api_generator is a CLI. It generates betterproto Python bindings from a
`.proto` file. It also writes a YAML data file to a protobuf binary file.

Use api_generator to make a configuration binary. Declare the configuration
fields in a `.proto` file. Write the values in a YAML file. api_generator
then writes the binary file for the device.

## Installation

api_generator is part of the `python/` uv workspace. uv installs it as an
editable dependency of `zephyr-python`. Run this command in the `python/`
directory:

```sh
uv sync
```

uv puts the `api_gen` script in the `PATH` of the venv. Keep the editable
installation. By default, api_generator writes the bindings in its own
installed directory. It then imports the bindings from that directory. Thus
the directory must permit write and import operations.

## Commands

`api_gen` has two subcommands.

| Command | Description                                                       |
|---------|-------------------------------------------------------------------|
| `build` | Generates betterproto Python bindings from a `.proto` file.       |
| `write` | Generates the bindings. Then writes a YAML file to a binary file. |

The `write` command does the `build` operation first. Thus use `build` only
if you do not want a binary file.

### Global options

Put these options before the subcommand. Example:
`api_gen --loglevel=debug write ...`

| Option            | Description                                                                                                                |
|-------------------|----------------------------------------------------------------------------------------------------------------------------|
| `-i`, `--include` | A proto include path. You can use this option more than one time.                                                          |
| `--libpath`       | The directory for the bindings. The name must end with `lib`. The default is `api_generator/lib` in the installed package. |
| `--pkgname`       | The Python package that contains the bindings. The default is `api_generator`.                                             |
| `--loglevel`      | The log level. The default is `info`.                                                                                      |

### The build command

```sh
api_gen -i $PROTO_BASE/MyConfig build $PROTO_BASE/MyConfig/MyConfig.proto
```

The command runs `protoc` with the betterproto plugin. It writes the result
to the `--libpath` directory. The command also includes the `.proto` files of
`grpc_tools`. These files supply `google/protobuf/*`.

> **The command deletes the lib directory at each start.** Set `--libpath` to
> a directory that contains generated files only. Do not set it to a
> directory that contains your own source files.

### The write command

```sh
api_gen -i $PROTO_BASE/MyConfig write \
    --mod myconfig \
    --msgcls Config \
    --yamlfile config.yaml \
    --out config.bin \
    $PROTO_BASE/MyConfig/MyConfig.proto
```

| Option       | Required | Description                                                             |
|--------------|----------|-------------------------------------------------------------------------|
| `--yamlfile` | yes      | The YAML file that contains the values.                                 |
| `--out`      | yes      | The binary output file. The command creates the parent directories.     |
| `--msgcls`   | no       | The name of the top message class.                                      |
| `--mod`      | no       | The module in the generated lib directory. The class is in this module. |

The command reads the YAML file with `yaml.safe_load`. It sends the data to
the betterproto `from_dict()` method. Then it writes the message with
`SerializeToString()`. Thus the YAML keys must agree with the proto field
names.

## How the command imports the bindings

The `--libpath` value and the `--pkgname` value must agree.

The `write` command imports the message class with one of these names:

```
<pkgname>.lib.<mod>     # if you use --mod
<pkgname>.lib           # if you do not use --mod
```

The default `--pkgname` value is `api_generator`. The default `--libpath`
value is the `lib` directory of that package. Thus the two default values
agree and you can omit both options.

If you set `--libpath` to a different package, you must also set `--pkgname`.
If you do not set `--pkgname`, the command cannot import the class. The
`--libpath` directory must have the name `lib`. The directory must also be in
a package that Python can import.

betterproto writes one module for each proto package. The `--mod` option
selects the module. betterproto writes messages that have no proto package to
the `lib` directory. Omit `--mod` for these messages.

## Comparison with proto_builder

api_generator and `proto_builder` both generate betterproto bindings. Use
them for different tasks.

|        | `api_generator`                                | `proto_builder`                                                      |
|--------|------------------------------------------------|----------------------------------------------------------------------|
| Start  | You start it from the CLI.                     | The build system starts it.                                          |
| Task   | Writes configuration data to a binary file.    | Builds the RPC callset packages. Examples: `systemrpc`, `rtosutils`. |
| Output | A `lib` directory and an optional `.bin` file. | A `lib` directory in the package.                                    |

Use `proto_builder` for a new RPC callset package. Use api_generator for a
single generation task and for configuration binary files.

## Package contents

| Path                        | Description                                      |
|-----------------------------|--------------------------------------------------|
| `api_generator/main.py`     | The `api_gen` CLI. Contains `build` and `write`. |
| `api_generator/__init__.py` | The log setup for the CLI.                       |

git does not track the generated files. `python/.gitignore` contains
`**/lib`.
