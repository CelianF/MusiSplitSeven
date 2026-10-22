"""Command-line front end to the core, for testing without the UI.

    python -m musisplit.cli "song.mp3" -o out/
    python -m musisplit.cli "https://www.youtube.com/watch?v=..." -o out/
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .errors import UserError
from .model import ensure_model
from .paths import default_output_dir
from .pipeline import Stage, run_job


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="musisplit")
    p.add_argument("source", help="audio file or YouTube URL")
    p.add_argument("-o", "--out", type=Path, default=default_output_dir())
    args = p.parse_args(argv)

    try:
        ensure_model(lambda done, total: print(f"\rmodel {done / total:6.1%}", end="", flush=True))
        print()
        last = [None, -1.0]

        def progress(stage: Stage, frac: float | None, msg: str) -> None:
            pct = -1.0 if frac is None else round(frac * 100)
            if (stage, pct) != tuple(last):
                last[:] = [stage, pct]
                shown = "…" if frac is None else f"{pct:3.0f}%"
                print(f"\r[{stage.value:11s}] {shown:>4s} {msg:40s}", end="", flush=True)

        t0 = time.perf_counter()
        result = run_job(args.source, args.out, progress)
        print(f"\ndone in {time.perf_counter() - t0:.1f}s\n  {result.vocals}\n  {result.instrumental}")
        return 0
    except UserError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
