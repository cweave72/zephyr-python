import sys
import os.path as osp
import logging
import shutil
import click
from typing import List
from pathlib import Path
from dataclasses import dataclass
from rich.logging import RichHandler

from google.protobuf.compiler.plugin_pb2 import (
    CodeGeneratorResponse,
    CodeGeneratorRequest,
)
from google.protobuf.descriptor_pb2 import (
    FileDescriptorProto,
)
from jinja2 import Environment
import pkg_resources
import grpc_tools.protoc as protoc

from protorpc import setup_logging

logger = logging.getLogger(__name__)

thisdir = osp.dirname(__file__)

# Plugin ####################################################

HEADER_TEMPLATE = """\
/*******************************************************************************
 *  @file: {{ proto_name }}.h
 *
 *  @brief: Header for {{ proto_name }}.
*******************************************************************************/
#ifndef {{ proto_name_caps }}_H
#define {{ proto_name_caps }}_H

#include <stdint.h>
#include "ProtoRpc.h"

/******************************************************************************
    [docexport {{ proto_name }}_resolver]
*//**
    @brief Resolver function for {{ proto_name }}.
    @param[in] call_frame  Pointer to the unpacked call frame object.
    @param[out] which_msg  Output which_msg was requested.
******************************************************************************/
ProtoRpc_handler *
{{ proto_name }}_resolver(void *call_frame, uint32_t offset);
#endif
"""

SOURCE_TEMPLATE = """\
/*******************************************************************************
 *  @file: {{ proto_name }}.c
 *
 *  @brief: Handlers for {{ proto_name }}.
*******************************************************************************/
#include <zephyr/logging/log.h>
#include "{{ proto_name }}.h"
#include "{{ proto_name }}.pb.h"

LOG_MODULE_REGISTER({{ proto_name }}, CONFIG_{{ proto_name_caps }}_LOG_LEVEL);

{% for handler in handlers -%}
/******************************************************************************
    {{ handler.call_func }}

    Call params:
    {%- for field in handler.call_fields %}
        call->{{ field.name }}: {{ field.type }} {{ field.label }}
    {%- endfor %}
    Reply params:
    {%- for field in handler.reply_fields %}
        reply->{{ field.name }}: {{ field.type }} {{ field.label }}
    {%- endfor %}
*//**
    @brief Implements the RPC {{ handler.call_func }} handler.
******************************************************************************/
static void
{{ handler.call_func }}(void *call_frame, void *reply_frame, StatusEnum *status)
{
    {{ handler.callset_type }} *call_msg = ({{ handler.callset_type }} *)call_frame;
    {{ handler.callset_type }} *reply_msg = ({{ handler.callset_type }} *)reply_frame;
    {{ handler.call_type }} *call = &call_msg->msg.{{ handler.call_name }};
    {{ handler.reply_type }} *reply = &reply_msg->msg.{{ handler.reply_name }};

    (void)call;
    (void)reply;

    LOGPRINT_DEBUG("In {{handler.call_func }} handler");

    reply_msg->which_msg = {{ handler.callset_type }}_{{ handler.reply_name }}_tag;
    *status = StatusEnum_RPC_SUCCESS;

    /* TODO: Implement handler */
}

{% endfor %}

static ProtoRpc_Handler_Entry handlers[] = {
{%- for handler in handlers %}
    PROTORPC_ADD_HANDLER({{ handler.callset_type }}_{{ handler.call_name }}_tag, {{ handler.call_func }}),
{%- endfor %}
};

#define NUM_HANDLERS    PROTORPC_ARRAY_LENGTH(handlers)

/******************************************************************************
    [docimport {{ proto_name }}_resolver]
*//**
    @brief Resolver function for {{ proto_name }}.
    @param[in] call_frame  Pointer to the unpacked call frame object.
    @param[out] which_msg  Output which_msg was requested.
******************************************************************************/
ProtoRpc_handler *
{{ proto_name }}_resolver(void *call_frame, uint32_t *which_msg)
{
    {{ package }}_{{ callset_type }} *this = ({{ package }}_{{ callset_type }} *)call_frame;
    unsigned int i;

    *which_msg = this->which_msg;

    /** @brief Handler lookup */
    for (i = 0; i < NUM_HANDLERS; i++)
    {
        ProtoRpc_Handler_Entry *entry = &handlers[i];
        if (entry->tag == this->which_msg)
        {
            return entry->handler;
        }
    }

    return NULL;
}
"""


def type_str(arg: int) -> str:
    return {
        1: "double",
        2: "float",
        3: "int64",
        4: "uint64",
        5: "int32",
        6: "fixed64",
        7: "fixed32",
        8: "bool",
        9: "string",
        10: "group",
        11: "message",
        12: "bytes",
        13: "uint32",
        14: "enum",
        15: "sfixed32",
        16: "sfixed64",
        17: "sint32",
        18: "sint64"
    }.get(arg, "unknown")


def label_str(arg: int) -> str:
    return {
        1: "optional",
        2: "required",
        3: "repeated",
    }.get(arg, "unknown")


@dataclass
class FieldType:
    name: str
    type: str
    label: str

    def __post_init__(self):
        self.label = "" if self.label == 'optional' else f"[{self.label}]"


@dataclass
class Handler:
    package: str
    callset_type: str
    call_name: str
    call_type: str
    call_fields: List[FieldType]
    reply_fields: List[FieldType]

    def __post_init__(self):
        self.callset_type = f"{self.package}_{self.callset_type}"
        self.call_func = self.call_name.rstrip('_call')
        self.call_type = self.call_type[1:].replace('.', '_')
        self.reply_name = self.call_name.replace('call', 'reply')
        self.reply_type = self.call_type.replace('call', 'reply')
        #self.reply_tag = f"{self.package}_{self.callset_type}_{self.reply_name}_tag"


def process_file(file_descr: FileDescriptorProto):
    """Processes a proto file and generates source content.
    """
    proto_name = file_descr.name.rstrip('.proto')
    package = file_descr.package

    logger.debug(f"proto_path={file_descr.name}")
    logger.debug(f"syntax={file_descr.syntax}")
    logger.debug(f"depends={file_descr.dependency}")
    logger.debug(f"package={file_descr.package}")
    logger.debug(f"enums={file_descr.enum_type}")

    msgs = {}
    handlers = []
    for msg in file_descr.message_type:
        logger.debug(f"msg: {msg.name}")

        # Are we in the callset body oneof message? Collect handlers.
        if msg.oneof_decl:
            oneof_name = msg.oneof_decl[0].name
            logger.debug(f"oneof: {oneof_name}")

            if oneof_name == 'msg':
                callset_type = msg.name

            # Callset message.  Collect call.
            for field in msg.field:
                if field.name.endswith('_call'):
                    logger.debug(f"   field[{field.number}]: {field.name} {label_str(field.label)} "
                                 f"(type={type_str(field.type)} {field.type_name})")
                    msg_type = field.type_name.split('.')[-1]
                    reply_type = msg_type.replace('call', 'reply')
                    handlers.append(Handler(package=file_descr.package,
                                            callset_type=callset_type,
                                            call_name=field.name,
                                            call_type=field.type_name,
                                            call_fields=msgs.get(msg_type, []),
                                            reply_fields=msgs.get(reply_type, []),
                                            ))

        # If we are in the message declarations, collect field info.
        else:
            fields = []
            for field in msg.field:
                logger.debug(f"   field[{field.number}]: {field.name} {label_str(field.label)} "
                             f"(type={type_str(field.type)} {field.type_name})")
                fields.append(FieldType(name=field.name,
                                        type=type_str(field.type),
                                        label=label_str(field.label)
                                        ))

            msgs[msg.name] = fields

    logger.debug(f"handlers={handlers}")

    files = []
    for templ, ext in [(SOURCE_TEMPLATE, '.c'), (HEADER_TEMPLATE, '.h')]:
        gen_file = CodeGeneratorResponse.File()
        gen_file.name = file_descr.name.replace(".proto", ext)

        template = Environment().from_string(templ)
        if ext == '.c':
            source = template.render(proto_name=proto_name,
                                     proto_name_caps=proto_name.upper(),
                                     package=package,
                                     callset_type=callset_type,
                                     handlers=handlers)
        else:
            source = template.render(proto_name=proto_name,
                                     proto_name_caps=proto_name.upper())

        gen_file.content = source
        files.append(gen_file)

    return files


def generator_main():
    """Generator plugin entrypoint.
    """
    # Setup logging within the plugin.
    rlogger = logging.getLogger()
    # Read level and path information stored in the .genlog file which was
    # written when the cli was invoked.
    log_params = Path().joinpath(thisdir, '.genlog').read_text()
    level, path = log_params.split(',')
    setup_logging(rlogger, level=level, logfile=osp.join(path, 'protorpc_out.log'))

    logger.info("Running protorpc generator plugin.")
    request = CodeGeneratorRequest.FromString(sys.stdin.buffer.read())
    response = CodeGeneratorResponse()

    for proto_file in request.proto_file:
        logger.debug(f"proto_file.name={proto_file.name}: {request.file_to_generate}")
        if proto_file.name in request.file_to_generate:
            logger.info(f"Processing file {proto_file.name}")
            files = process_file(proto_file)
            for f in files:
                logger.info(f"Output file --> {f.name}")
                response.file.append(f)

    sys.stdout.buffer.write(response.SerializeToString())


# Cli  ####################################################

def run_protoc(proto_path, includes, out_path):
    """Function which invokes protoc with the plugin functionality defined above.
    """
    if not isinstance(includes, list):
        includes = [includes]

    proto_include = pkg_resources.resource_filename("grpc_tools", "_proto")

    fmt_includes = [f"-I{proto_include}"]
    fmt_includes += [f"-I{inc}" for inc in includes]
    cmd_str = fmt_includes
    cmd_str += [proto_path]
    # Specifying --protorpc_out assumes there is a binary named
    # protoc-gen-protorpc present in the PATH.
    cmd_str += [f"--protorpc_out={out_path}"]

    logger.debug(f"Cleaning dir: {out_path}")
    if Path(out_path).exists():
        shutil.rmtree(out_path)
    Path(out_path).mkdir(parents=True)

    logger.debug(f"cmd_str = {cmd_str}")
    try:
        ret = protoc.main(cmd_str)
        if ret == 1:
            logger.error("Error: protoc returned error.")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error in grpc_tools.main(): {str(e)}")
        sys.exit(1)


def get_params(**kwargs):
    """Converts kwargs to Params class.
    """

    class Params:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    return Params(**kwargs)


@click.command()
@click.argument("protofile")
@click.option("--loglevel", default='info', help="Debug logging level.")
@click.option("--gen-loglevel", default='info', help="Debug logging level for generator plugin.")
@click.option("--debug", is_flag=True, help="Shortcut for --loglevel=debug")
@click.option("-i", "--include", multiple=True, help="Include path (can provide multiple).")
@click.option("--outpath", help="Path to where generated files will be placed (defaults to cwd).")
def cli(**kwargs):
    """Cli for generating ProtoRpc C handler source.
    """
    params = get_params(**kwargs)
    rlogger = logging.getLogger()

    if params.debug:
        params.loglevel = 'debug'

    if params.outpath is None:
        params.outpath = osp.join(Path().cwd(), 'protorpc_out')

    gen_log_level = Path().joinpath(thisdir, '.genlog')
    gen_log_level.write_text(f"{params.gen_loglevel},{params.outpath}")

    setup_logging(rlogger, level=params.loglevel)
    logger.debug("Running run_protorpc_gen entry.")

    if isinstance(params.include, tuple):
        params.include = list(params.include)
    else:
        params.include = [params.include]

    logger.debug(f"cli_params={params.__dict__}")
    run_protoc(params.protofile, params.include, params.outpath)


def entrypoint():
    cli()


if __name__ == "__main__":

    rlogger = logging.getLogger()
    rlogger.setLevel(logging.DEBUG)
    ch = RichHandler(rich_tracebacks=True, show_time=False)
    ch.setLevel(logging.DEBUG)
    rlogger.addHandler(ch)
    entrypoint()
