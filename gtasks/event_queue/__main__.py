from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .broker import provision
from .ops import redrive
from .producer import enqueue_once
from .runtime import RuntimeLayout, initialize_runtime
from .service import run_consumer


def _layout(value: str | None) -> RuntimeLayout:
    return RuntimeLayout(Path(value).expanduser()) if value else RuntimeLayout()


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate the GTasks event queue.")
    parser.add_argument("--runtime-root")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup")
    commands.add_parser("provision")
    consumer = commands.add_parser("consumer")
    consumer.add_argument("--health-host", default="127.0.0.1")
    consumer.add_argument("--health-port", type=int, default=4181)
    producer = commands.add_parser("enqueue")
    producer.add_argument("--event-file", required=True)
    redrive_parser = commands.add_parser("redrive")
    redrive_parser.add_argument("--event-id", required=True)
    redrive_parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    layout = _layout(args.runtime_root)
    if args.command == "setup":
        initialized = initialize_runtime(layout)
        print(json.dumps({"runtime_root": str(initialized.root)}, sort_keys=True))
    elif args.command == "provision":
        print(json.dumps(asyncio.run(provision(layout)), sort_keys=True))
    elif args.command == "consumer":
        asyncio.run(
            run_consumer(
                layout,
                health_host=args.health_host,
                health_port=args.health_port,
            )
        )
    elif args.command == "enqueue":
        envelope = json.loads(Path(args.event_file).read_text(encoding="utf-8"))
        result = asyncio.run(
            enqueue_once(envelope, binding_path=layout.producer_binding)
        )
        print(json.dumps(result.safe_dict(), sort_keys=True))
    elif args.command == "redrive":
        print(
            json.dumps(
                asyncio.run(
                    redrive(
                        args.event_id,
                        layout=layout,
                        confirmed=args.confirm,
                    )
                ),
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
