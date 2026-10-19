"""__NAME__ command-line entry point.

The day-1 vertical slice: a ``hello`` command on argparse with pw-foundation's
logging. Replace it with your real commands.
"""

from __future__ import annotations

import argparse

from pw_foundation.log import get_logger, setup_logging

from __PKG__.config import cfg

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="__NAME__", description="__NAME__ — a pw-foundation tool."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    hello = commands.add_parser("hello", help="Greet someone — proves the install + wiring works.")
    hello.add_argument("name", nargs="?", default="world")
    args = parser.parse_args(argv)
    setup_logging()
    logger.info("hello_invoked", name=args.name)
    print(f"hello, {args.name} (log level: {cfg.LOG_LEVEL})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
