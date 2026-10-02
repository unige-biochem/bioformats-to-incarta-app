"""Convert one Bio-Formats readable file into a dataset IN Carta can import.

    bioformats-to-incarta plate.nd2 converted/
    bioformats-to-incarta plate.lif converted/ --series 2 --compression LZW

This runs the same SciJava command as Fiji's *Plugins > UNIGE > Bio-Formats to
IN Carta*, without Fiji: the first run downloads a JDK and the converter's jars
(`--prepare` does only that), every later run starts in a few seconds.

Two readers that do better than Bio-Formats' own are always included: BIOP's
quick-start reader for Zeiss .czi, and the SlideBook 6 reader for .sld (fetched
once from the SlideBook update site). `--prepare` checks both are picked up.

Ctrl+C stops the conversion before its next plane; a second Ctrl+C kills it.
Either way the output folder is left without its .xdce index, so a stopped
conversion cannot be imported by mistake.

Exit codes: 0 converted, 1 failed, 2 bad arguments, 3 stopped.
"""

from __future__ import annotations

import argparse
import queue
import sys
from pathlib import Path

from . import __version__, launcher
from .launcher import Conversion, Finished, Options, Output, Progress


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bioformats-to-incarta", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="?", type=Path,
                        help="any file Bio-Formats can open")
    parser.add_argument("output", nargs="?", type=Path,
                        help="where the converted dataset is written")
    parser.add_argument("--series", type=int, default=-1,
                        help="series to convert; -1 (default) converts them all")
    parser.add_argument("--compression", choices=launcher.COMPRESSIONS,
                        default="Uncompressed", help="default: Uncompressed")
    parser.add_argument("--bigtiff", action="store_true",
                        help="needed for planes above the 4 GB TIFF limit")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace files already in the output folder")
    parser.add_argument("--memory", metavar="SIZE",
                        help="maximum Java heap, e.g. 8G; default: chosen by jgo")
    parser.add_argument("--add-jar", metavar="JAR", type=Path, action="append",
                        default=[],
                        help="one more jar on the class path, e.g. another "
                             "Bio-Formats reader; may be repeated. The CZI and "
                             "SlideBook readers are always there already")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show the libraries' own output as it comes")
    parser.add_argument("--prepare", action="store_true",
                        help="only fetch Java and the converter, then exit")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__} ({launcher.CONVERTER})")
    return parser


def options_from(args: argparse.Namespace,
                 parser: argparse.ArgumentParser) -> Options:
    if args.input is None or args.output is None:
        parser.error("INPUT and OUTPUT are both needed (or --prepare)")
    if not args.input.is_file():
        parser.error(f"{args.input} is not a file")
    if args.output.exists() and not args.output.is_dir():
        parser.error(f"{args.output} exists and is not a folder")
    if args.series < -1:
        parser.error("--series is -1 (all) or a series index from 0")
    for jar in args.add_jar:
        if not jar.is_file():
            parser.error(f"--add-jar {jar} is not a file")
    return Options(input=args.input.resolve(), output=args.output.resolve(),
                   series=args.series, compression=args.compression,
                   big_tiff=args.bigtiff, overwrite=args.overwrite,
                   max_heap=args.memory,
                   extra_classpath=[jar.resolve() for jar in args.add_jar])


class ProgressLine:
    """One line rewritten in place on a terminal; a line per 5% otherwise."""

    def __init__(self) -> None:
        self.tty = sys.stdout.isatty()
        self.shown = -1
        self.open = False

    def show(self, event: Progress) -> None:
        percent = 100 * event.done // max(event.total, 1)
        text = f"[{event.done:>{len(str(event.total))}}/{event.total}] {event.message}"
        if self.tty:
            print(f"\r{text[:110]:<110}", end="", flush=True)
            self.open = True
        elif percent >= self.shown + 5 or event.done == event.total:
            self.shown = percent
            print(text, flush=True)

    def close(self) -> None:
        if self.open:
            print(flush=True)
            self.open = False


def run(options: Options, verbose: bool) -> int:
    print(f"{options.input}\n  -> {options.output}", flush=True)
    conversion = Conversion(options).start()
    line = ProgressLine()
    interrupts = 0
    while True:
        try:
            # A timeout, because a bare get() cannot be interrupted on Windows.
            event = conversion.events.get(timeout=0.25)
        except queue.Empty:
            continue
        except KeyboardInterrupt:
            interrupts += 1
            line.close()
            if interrupts == 1:
                print("Stopping after the current plane (Ctrl+C again to kill)...",
                      file=sys.stderr, flush=True)
                conversion.stop()
            else:
                conversion.kill()
            continue

        if isinstance(event, Progress):
            line.show(event)
        elif isinstance(event, Output):
            if verbose:
                line.close()
                print(event.line, flush=True)
        elif isinstance(event, Finished):
            line.close()
            if event.status == "done":
                print(f"Done: {event.message}.")
                return launcher.EXIT_DONE
            if event.status == "stopped" or interrupts:
                print(f"Stopped: {event.message}. {options.output} holds a partial "
                      f"dataset, without its .xdce index.", file=sys.stderr)
                return launcher.EXIT_STOPPED
            if not verbose:
                sys.stderr.write("\n".join(event.tail) + "\n")
            print(f"Failed: {event.message}", file=sys.stderr)
            return launcher.EXIT_FAILED


def prepare() -> int:
    print(f"Fetching Java and {launcher.CONVERTER} (only slow the first time)...",
          flush=True)
    try:
        for line in launcher.prepare():
            print(line, flush=True)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return launcher.EXIT_FAILED
    return launcher.EXIT_DONE


def main(argv: list[str] | None = None) -> int:
    # The libraries' output is echoed as it comes; a character the console's
    # code page lacks must print as "?", not end the run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.prepare:
        return prepare()
    return run(options_from(args, parser), args.verbose)


if __name__ == "__main__":
    sys.exit(main())
