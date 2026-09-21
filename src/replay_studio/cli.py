"""Replay Studio command line entry points."""

import argparse
import logging

from .config import Config


def main() -> None:
    parser = argparse.ArgumentParser(prog="replay")
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    worker = sub.add_parser("worker")
    worker.add_argument("--queue", choices=["all", "cpu", "gpu"], default="all")
    mode = worker.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--drain", action="store_true")
    sub.add_parser("init")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.command == "serve":
        import uvicorn

        from .api import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
    else:
        from .store import Store
        store = Store(Config.from_env())
        if args.command == "worker":
            from .worker import run_worker
            run_worker(store, queue=args.queue, once=args.once, drain=args.drain)
        else:
            print("Database and object storage initialized.")


if __name__ == "__main__":
    main()
