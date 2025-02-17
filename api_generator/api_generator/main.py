import os.path as osp
import shutil
import sys
import click
import logging
import importlib
import yaml
from pathlib import Path
from rich import inspect

import pkg_resources
import grpc_tools.protoc as protoc

from api_generator import setup_logging

logger = logging.getLogger(__name__)

thisdir = osp.dirname(__file__)
libdir = osp.join(thisdir, 'lib')
thismod = osp.splitext(osp.basename(__file__))[0]


def api_build(proto_path, includes, libpath=None):
    """Builds the python api locally.
    """
    if not isinstance(includes, list):
        includes = [includes]

    if libpath is None:
        logger.debug(f"Using default libpath={libdir}")
        libpath = libdir
    else:
        logger.debug(f"Using libpath={libpath}")

    proto_include = pkg_resources.resource_filename("grpc_tools", "_proto")

    fmt_includes = [f"-I{proto_include}"]
    fmt_includes += [f"-I{inc}" for inc in includes]
    cmd_str = fmt_includes + [f"--python_betterproto_out={libpath}"] + [proto_path]

    logger.debug(f"Cleaning dir: {libpath}")
    if Path(libpath).exists():
        shutil.rmtree(libpath)
    Path(libpath).mkdir(parents=True)

    logger.debug(f"cmd_str = {cmd_str}")
    try:
        ret = protoc.main(cmd_str)
        if ret == 1:
            logger.error("Error: protoc returned error.")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error in grpc_tools.main(): {str(e)}")
        sys.exit(1)


def api_write(cls_name, data, out, mod_name=None, pkg_name=None):
    """Writes a serialized binary based on the pre-build api and data yaml.
    """

    logger.debug(f"module name={mod_name}; class={cls_name}; pkg={pkg_name}")
    logger.debug(f"__package__={__package__}")

    if pkg_name is None:
        logger.debug(f"Using default pkg_name={__package__}")
        pkg_name = __package__
    else:
        logger.debug(f"Using pkg_name={pkg_name}")

    if mod_name is not None:
        logger.info(f"Importing module: {pkg_name}.lib.{mod_name}")
        mod = importlib.import_module(f"{pkg_name}.lib.{mod_name}")
    else:
        logger.info(f"Importing module: {pkg_name}.lib")
        mod = importlib.import_module(f"{pkg_name}.lib")

    try:
        cls = getattr(mod, cls_name)
        inst = cls()
        inst.from_dict(data)
        #inspect(inst)
        logger.info(f"Protobuf message: {inst.to_dict()}")
        serialized = inst.SerializeToString()
    except Exception as e:
        logger.exception(f"Error loading dataclass: {str(e)}")
        sys.exit(1)

    outfile = Path(out)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Writing {outfile.absolute()}")
    with outfile.open('wb') as f:
        f.write(serialized)


def get_params(**kwargs):
    """Converts kwargs to Params class.
    """

    class Params:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    return Params(**kwargs)


@click.group()
@click.option("--loglevel", default='info', help="Debug logging level.")
@click.option("-i", "--include", multiple=True, help="Include path (can provide multiple).")
@click.option("--libpath", help="Path to generated python api library (must end with .../lib).")
@click.option("--pkgname", help="Python package name where lib is generated (i.e. package where lib was generated).")
@click.pass_context
def cli(ctx, **kwargs):

    params = get_params(**kwargs)
    rlogger = logging.getLogger()
    setup_logging(rlogger, level=params.loglevel)

    if isinstance(params.include, tuple):
        params.include = list(params.include)
    else:
        params.include = [params.include]

    logger.debug(f"cli_params={params.__dict__}")
    ctx.obj['cli_params'] = params


@cli.command
@click.argument("protofile")
@click.pass_context
def build(ctx, **kwargs):
    """Builds protobuf python api.
    """
    params = get_params(**kwargs)
    cli_params = ctx.obj['cli_params']

    logger.info(f"--> Building api for {osp.basename(params.protofile)}")
    api_build(params.protofile, cli_params.include, cli_params.libpath)


@cli.command
@click.argument("protofile")
@click.option("--mod", help="Protobuf api module name.")
@click.option("--msgcls", help="Protobuf top message class name.")
@click.option("--yamlfile", required=True, help="YAML file with data.")
@click.option("--out", required=True, help="Serialized output file.")
@click.pass_context
def write(ctx, **kwargs):
    """Writes configuration binary from config proto file.
    """
    params = get_params(**kwargs)
    cli_params = ctx.obj['cli_params']
    logger.debug(f"params={params.__dict__}")

    logger.info(f"--> Building api for {osp.basename(params.protofile)}")
    api_build(params.protofile, cli_params.include, cli_params.libpath)

    logger.info(f"--> Writing binary {params.out} based on {params.yamlfile}")
    with open(params.yamlfile, 'r') as f:
        data = yaml.safe_load(f)
        logger.info(f"data={data}")

    api_write(params.msgcls, data, params.out, mod_name=params.mod,
              pkg_name=cli_params.pkgname)


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
