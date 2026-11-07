"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import __version__
from .config import load_config
from .doctor import doctor, show_config, validate_config
from .hook import handle_seed, run as run_hook
from .install import install, uninstall


def _add_common_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="config path (default: ~/.claude/genai-hook.json)")
    parser.add_argument("--settings", help="Claude settings path (default: ~/.claude/settings.json)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cc-genai-hook",
        description="Generic Claude Code GenAI hook: export gen_ai spans to OTLP",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    install_p = sub.add_parser("install", help="install command-mode hooks")
    install_p.add_argument("--mode", choices=["command", "sidecar", "auto"], default="command")
    install_p.add_argument("--endpoint", help="OTLP endpoint")
    install_p.add_argument(
        "--endpoint-kind",
        choices=["base", "full"],
        default="base",
        help="base appends /v1/traces; full uses the endpoint as-is",
    )
    install_p.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="OTLP header; repeatable",
    )
    install_p.add_argument("--user", help="value for enduser.id")
    install_p.add_argument(
        "--content-mode", choices=["last_turn", "full", "none"], default="last_turn"
    )
    install_p.add_argument("--dry-run", action="store_true")
    _add_common_config(install_p)
    install_p.set_defaults(func=install)

    uninstall_p = sub.add_parser("uninstall", help="remove installed hooks")
    uninstall_p.add_argument("--purge", action="store_true", help="also remove config and state")
    uninstall_p.add_argument("--dry-run", action="store_true")
    _add_common_config(uninstall_p)
    uninstall_p.set_defaults(func=uninstall)

    doctor_p = sub.add_parser("doctor", help="check config, dependencies and hooks")
    doctor_p.add_argument("--send-test", action="store_true", help="send a test span")
    _add_common_config(doctor_p)
    doctor_p.set_defaults(func=doctor)

    hook_p = sub.add_parser("hook", help="internal hook entrypoint")
    hook_p.add_argument(
        "event",
        choices=[
            "stop",
            "session_end",
            "subagent_stop",
            "post_tool_use",
            "post_tool_use_failure",
            "seed",
        ],
    )
    hook_p.set_defaults(func=lambda args: run_hook(args.event))

    config_p = sub.add_parser("config", help="config utilities")
    config_sub = config_p.add_subparsers(dest="config_command")
    show_p = config_sub.add_parser("show", help="print effective config")
    show_p.add_argument("--redact", action="store_true", default=True)
    _add_common_config(show_p)
    show_p.set_defaults(func=show_config)
    validate_p = config_sub.add_parser("validate", help="validate endpoint/headers")
    _add_common_config(validate_p)
    validate_p.set_defaults(func=validate_config)

    seed_p = sub.add_parser("seed", help="mark existing transcript messages as processed")
    seed_p.add_argument("--session-id", required=True)
    seed_p.add_argument("--transcript", required=True)
    _add_common_config(seed_p)
    seed_p.set_defaults(func=_seed)

    return parser


def _seed(args: argparse.Namespace) -> int:
    cfg = load_config(getattr(args, "config", None), require_endpoint=False)
    result = handle_seed(
        {"session_id": args.session_id, "transcript_path": args.transcript}, cfg
    )
    if result.error:
        print("error: %s" % result.error, file=sys.stderr)
        return 1
    print("[ok] seeded %d assistant message(s)" % result.emitted)
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    if args.command == "config" and not getattr(args, "config_command", None):
        parser.parse_args(["config", "--help"])
        return 1
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
