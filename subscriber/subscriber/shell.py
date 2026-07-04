import argparse
import cmd
import shlex

from subscriber import client, sysmsg


class _ArgumentParserNoExit(argparse.ArgumentParser):
    """An ArgumentParser that raises instead of calling sys.exit() on error,
    so a bad command invocation doesn't kill the whole shell."""

    def error(self, message):
        raise ValueError(message)


def _add_pub_group(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--all", action="store_true",
        help="Subscribe to all publishers (default).")
    group.add_argument(
        "--pub", type=int,
        help="Subscribe to a single publisher id (its IP's last octet).")
    return group


def _resolve_topic(base_topic, args):
    if args.pub is not None:
        return f"{base_topic}/pubid-{args.pub}"
    return f"{base_topic}/+"


class SubscriberShell(cmd.Cmd):
    intro = "Zephyr MQTT subscriber shell. Type help or ? to list commands.\n"
    prompt = "(subscriber) "

    def __init__(self, host, port):
        super().__init__()
        self.host = host
        self.port = port

        self._sub_parser = _ArgumentParserNoExit(prog="sub", add_help=False)
        self._sub_parser.add_argument("topic")
        _add_pub_group(self._sub_parser)

        self._sysmsg_parser = _ArgumentParserNoExit(prog="sysmsg", add_help=False)
        self._sysmsg_parser.add_argument(
            "--level", choices=sysmsg.LEVEL_CHOICES, default="debug")
        _add_pub_group(self._sysmsg_parser)

    def _parse(self, parser, arg):
        try:
            return parser.parse_args(shlex.split(arg))
        except (ValueError, SystemExit) as exc:
            print(f"usage: {parser.format_usage().strip()}")
            if str(exc):
                print(exc)
            return None

    def do_sub(self, arg):
        """sub TOPIC [--all | --pub=ID]

        Subscribe to an arbitrary TOPIC and stream best-effort decoded
        messages until Ctrl-C. Every Publisher-module topic is actually
        published as "TOPIC/pubid-<id>"; --pub targets one publisher, --all
        (the default) wildcards all of them.
        """
        args = self._parse(self._sub_parser, arg)
        if args is None:
            return

        topic = _resolve_topic(args.topic, args)
        client.SubscriberClient(self.host, self.port, topic).run()

    def do_sysmsg(self, arg):
        """sysmsg [--level {debug,info,warn,error,critical}] [--all | --pub=ID]

        Subscribe to the "sysmsg" topic and stream decoded system messages,
        filtered on console by --level, until Ctrl-C. Every message received
        is logged to sysmsg.log regardless of level. --pub targets one
        publisher, --all (the default) wildcards all of them.
        """
        args = self._parse(self._sysmsg_parser, arg)
        if args is None:
            return

        topic = _resolve_topic("sysmsg", args)
        sysmsg.MsgSubscriberClient(
            self.host, self.port, topic, level=args.level).run()

    def emptyline(self):
        """Do nothing on an empty line (cmd.Cmd's default repeats the last
        command, which would re-trigger a subscription right after Ctrl-C)."""
        pass

    def do_quit(self, arg):
        """Exit the shell."""
        return True

    def do_exit(self, arg):
        """Exit the shell."""
        return True

    def do_EOF(self, arg):
        """Exit the shell (Ctrl-D)."""
        print()
        return True
