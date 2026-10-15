"""Command line.

    python -m photomap                      start the map app (asks for a folder the first time)
    python -m photomap run --folder PATH    start the map app on a specific folder
    python -m photomap index PATH           only update the index, no app
"""

import argparse
import logging
import sys
from pathlib import Path

from .config import DEFAULT_DATA_DIR


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="photomap")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="start the map app (the default)")
    run_p.add_argument("--folder", type=Path, help="photo folder (remembered for next time)")
    run_p.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR)
    run_p.add_argument("--port", type=int, default=8050)
    run_p.add_argument("--no-browser", action="store_true", help="don't open a browser tab")

    idx = sub.add_parser("index", help="scan a photo folder and update the index")
    idx.add_argument("folder", type=Path, help="folder of photos (searched recursively)")
    idx.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR,
                     help=f"where the index and thumbnails live (default: {DEFAULT_DATA_DIR})")
    idx.add_argument("--workers", type=int, default=4)

    args = parser.parse_args(argv)

    if args.command == "index":
        from .indexer import Settings, index_folder

        logging.basicConfig(level=logging.INFO, format="%(message)s")

        def progress(done, total):
            if done == total or done % 100 == 0:
                print(f"  read {done:,} of {total:,} photos", flush=True)

        try:
            index_folder(Settings(args.folder, args.data, workers=args.workers), progress)  # logs its report
        except FileNotFoundError as e:
            print(e, file=sys.stderr)
            return 1
        return 0

    from .app import run

    try:
        run(photo_folder=getattr(args, "folder", None), data_dir=getattr(args, "data", DEFAULT_DATA_DIR),
            port=getattr(args, "port", 8050), open_browser=not getattr(args, "no_browser", False))
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
