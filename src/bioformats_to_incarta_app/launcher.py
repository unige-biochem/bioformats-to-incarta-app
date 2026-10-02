"""How the converter is launched: one jgo endpoint, one Groovy shim, one JVM per run.

The conversion itself is `BioformatsToIncartaCommand`, the SciJava command Fiji
shows as *Plugins > UNIGE > Bio-Formats to IN Carta*. None of Fiji is needed to
run it: `jgo` resolves the Maven endpoint below into a classpath, fetches a JDK
if the machine has none, and runs `run_command.groovy` against it. The shim
runs the command through the CommandService and narrates it on stdout, one
`B2I_*` line per thing worth knowing (see the shim's header).

Both the command line and the window go through `Conversion`, so there is one
place that knows how a JVM is started, read and stopped.
"""

from __future__ import annotations

import importlib.util
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

#: The converter. ImageJ is only a test dependency of it, so this resolves to
#: SciJava plus Bio-Formats and nothing else.
CONVERTER = "ch.unige.biochem:bioformats-to-incarta:1.0.0"

#: What `groovy.ui.GroovyMain` is. The version pom-scijava 45.1.0 manages, the
#: same parent the converter builds against.
GROOVY = "org.apache.groovy:groovy:4.0.28"

#: Bio-Formats logs through SLF4J, and nothing in its tree provides a backend:
#: without one its warnings vanish and the shim cannot set a log level. Fiji
#: ships logback; this is the version pom-scijava 45.1.0 manages.
LOGBACK = "ch.qos.logback:logback-classic:1.3.16"


@dataclass(frozen=True)
class ExternalReader:
    """A Bio-Formats reader that is better than the built-in one for a format.

    Bio-Formats' own `readers.txt` already names each of these, marked
    `[type=external]` and ahead of the reader it replaces: present on the class
    path, it is used; absent, it is skipped. So putting it on the class path is
    all it takes - `prepare()` checks that it really is picked up.
    """

    label: str
    reader_class: str
    #: Maven coordinates, when it is published: then it joins the endpoint.
    coordinates: str | None = None
    #: Otherwise a pinned download, fetched once into `jar_cache()`.
    url: str | None = None

    @property
    def jar(self) -> Path | None:
        """Where a downloaded reader is kept: named after its pinned version, so
        changing the pin fetches the new one instead of reusing the old."""
        if self.url is None:
            return None
        name = self.url.rsplit("/", 1)[-1]  # SlideBook6Reader.jar-20260903101721
        stem, _, stamp = name.partition(".jar-")
        return jar_cache() / (f"{stem}-{stamp}.jar" if stamp else name)


EXTERNAL_READERS = [
    ExternalReader("Zeiss CZI (BIOP quick start)",
                   "ch.epfl.biop.formats.in.ZeissQuickStartCZIReader",
                   coordinates="ch.epfl.biop:quick-start-czi-reader:0.3.0"),
    # Not on Maven; the SlideBook update site is where it lives. Without it,
    # Bio-Formats falls back to SlidebookReader, which on a large .sld does
    # not fail but grinds for over an hour. The jar carries its own native
    # libraries for Windows, macOS and Linux.
    ExternalReader("SlideBook 6", "loci.formats.in.SlideBook6Reader",
                   url="https://sites.imagej.net/SlideBook/jars/bio-formats/"
                       "SlideBook6Reader.jar-20260903101721"),
]

ENDPOINT = "+".join([CONVERTER, GROOVY, LOGBACK, *(reader.coordinates
                                          for reader in EXTERNAL_READERS
                                          if reader.coordinates)])
SCIJAVA_REPO = "https://maven.scijava.org/content/groups/public"
SHIM = Path(__file__).resolve().parent / "run_command.groovy"
PROBE = Path(__file__).resolve().parent / "probe.groovy"

#: The choices the command's dialog offers, in the same order.
COMPRESSIONS = ["Uncompressed", "LZW", "JPEG-2000", "JPEG-2000 Lossy", "zlib"]

#: What Fiji ships and the converter is developed on; left alone, jgo picks the
#: oldest Java the bytecode allows (11). Zulu is the vendor jgo fetches.
JAVA_VERSION, JAVA_VENDOR = "21", "zulu"

#: --class-path-only: every jar on the class path, the way Fiji runs them;
#: jgo would otherwise move the modular ones onto the module path.
JGO_FLAGS = ["--color", "plain", "--class-path-only", "--java-version", JAVA_VERSION,
             "--timeout", "120", "-r", f"scijava:{SCIJAVA_REPO}"]

#: Written by Install.cmd, next to the virtual environment: a JDK and every jar,
#: downloaded once by an administrator and read by every user of the machine.
#: When it exists, nothing goes to the user's own caches. It is read-only to
#: them, so a jar it lacks is not fetched: running Install.cmd again is the fix.
SHARED = Path(sys.prefix).parent / "java"
SHARED_JDK = SHARED / "jdk"

#: jgo 3.1.0 drops JVM arguments passed after its `--`, so they travel in the
#: environment instead. UTF-8 so that a file name with an accent comes back
#: through the pipe intact.
JAVA_TOOL_OPTIONS = ("-Djava.awt.headless=true -Dfile.encoding=UTF-8 "
                     "-Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8")
#: What the JVM prints about the line above on every start. Not worth showing.
JAVA_TOOL_OPTIONS_ECHO = "Picked up JAVA_TOOL_OPTIONS:"

#: The shim's exit codes.
EXIT_DONE, EXIT_FAILED, EXIT_STOPPED = 0, 1, 3

#: How much of the libraries' own output to keep for a failure report.
TAIL_LINES = 200


def shared() -> bool:
    return SHARED.is_dir()


def jar_cache() -> Path:
    """Downloaded jars live with the user's caches, never in the repository -
    or in the machine's shared folder, when there is one."""
    if shared():
        return SHARED / "jars"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA")
                    or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "bioformats-to-incarta-app" / "jars"


def fetch_jars(say: Callable[[str], None]) -> list[Path]:
    """Every downloaded reader, fetched now if it is not cached yet.

    A reader that cannot be fetched is reported and left out rather than
    failing the run: Bio-Formats then falls back on its built-in reader for
    that format, which still converts - slower, or less well.
    """
    jars = []
    for reader in EXTERNAL_READERS:
        jar = reader.jar
        if jar is None:
            continue
        if not jar.exists():
            say(f"Fetching the {reader.label} reader (once): {reader.url}")
            try:
                download(reader.url, jar)
            except OSError as error:
                say(f"WARNING: could not fetch the {reader.label} reader ({error}); "
                    f"Bio-Formats' built-in reader is used instead.")
                continue
            say(f"  saved {jar} ({jar.stat().st_size >> 20} MB)")
        jars.append(jar)
    return jars


def download(url: str, destination: Path) -> None:
    """To `<name>.part`, then renamed: an interrupted download is never mistaken
    for a finished one on the next run."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, part.open("wb") as out:
        shutil.copyfileobj(response, out)
    part.replace(destination)


def java_executable(home: Path) -> Path:
    return home / "bin" / ("java.exe" if os.name == "nt" else "java")


def fetch_shared_jdk(say: Callable[[str], None]) -> None:
    """Copy the JDK jgo would use into the shared folder, once.

    Not left in cjdk's cache, which would do: cjdk rewrites its index there
    every day, and the users cannot write to the shared folder. A plain JDK
    at a fixed place is what `_environment()` points jgo to instead.
    """
    if java_executable(SHARED_JDK).exists():
        return
    import cjdk

    say(f"Fetching Java {JAVA_VERSION} ({JAVA_VENDOR}) for every user (once)")
    home = cjdk.java_home(version=JAVA_VERSION, vendor=JAVA_VENDOR)
    part = SHARED_JDK.with_name(SHARED_JDK.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    shutil.copytree(home, part)
    # A virus scanner reading the new files keeps the folder from being
    # renamed for a few seconds.
    for attempt in range(60):
        try:
            part.replace(SHARED_JDK)
            break
        except PermissionError:
            if attempt == 59:
                raise
            time.sleep(1)
    say(f"  saved {SHARED_JDK}")


def jgo_command() -> list[str]:
    """jgo, preferably the one installed alongside this package."""
    if importlib.util.find_spec("jgo") is not None:
        return [sys.executable, "-m", "jgo"]
    found = shutil.which("jgo")
    if found:
        return [found]
    raise RuntimeError(
        "jgo is not installed. It is a dependency of this package, so\n"
        "    uv sync\n"
        "should bring it; `uv tool install 'jgo[cli]'` works as well."
    )


def _popen(argv: list[str]) -> subprocess.Popen:
    """A JVM whose output we read and whose stdin we write "stop" to.

    It gets a process group of its own, so that Ctrl+C in a console reaches
    only the Python side - which then asks the JVM to stop between two planes,
    rather than the JVM dying mid-plane. On Windows it also gets no console
    window, which matters when the caller is the window.
    """
    extra: dict = {}
    if os.name == "nt":
        extra["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                  | subprocess.CREATE_NO_WINDOW)
    else:
        extra["start_new_session"] = True
    return subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        bufsize=1, env=_environment(), **extra,
    )


def _environment() -> dict[str, str]:
    # jgo is Python too: writing to a pipe on Windows it would otherwise use the
    # ANSI code page, and its download bars hold characters that has not got.
    environment = dict(os.environ, JAVA_TOOL_OPTIONS=JAVA_TOOL_OPTIONS,
                       PYTHONIOENCODING="utf-8")
    if shared():
        # jgo takes a suitable Java it finds before asking cjdk for one. On
        # Windows it looks for `bin/java` without `.exe` under JAVA_HOME and so
        # never finds it there; first on the PATH, it does.
        environment["JAVA_HOME"] = str(SHARED_JDK)
        environment["PATH"] = os.pathsep.join(
            [str(java_executable(SHARED_JDK).parent), environment.get("PATH", "")])
    return environment


def _jgo_run(*, max_heap: str | None, extra_classpath: list[Path],
             groovy_args: list[str]) -> list[str]:
    heap = ["--max-heap", max_heap] if max_heap else []
    classpath = [arg for jar in extra_classpath
                 for arg in ("--add-classpath", str(jar))]
    # Flags, which win over a user's JGO_CACHE_DIR, M2_REPO and jgo.conf alike.
    caches = (["--cache-dir", str(SHARED / "jgo"), "--repo-cache", str(SHARED / "m2")]
              if shared() else [])
    # The two `--` are jgo's: the first ends jgo's arguments, the second the
    # JVM's, so what follows reaches GroovyMain.
    return [*jgo_command(), *JGO_FLAGS, *caches, *heap, "run",
            "--main-class", "groovy.ui.GroovyMain", *classpath,
            ENDPOINT, "--", "--", *groovy_args]


# --------------------------------------------------------------------------- #
#  What a run says
# --------------------------------------------------------------------------- #

@dataclass
class Progress:
    done: int
    total: int
    message: str


@dataclass
class Output:
    """A line from the libraries themselves: logs, warnings, stack traces."""

    line: str


@dataclass
class Finished:
    status: str  # "done", "stopped" or "failed"
    message: str
    planes: int | None = None
    returncode: int | None = None
    tail: list[str] = field(default_factory=list)


Event = Progress | Output | Finished


@dataclass
class Options:
    """The command's parameters, plus what the JVM itself needs."""

    input: Path
    output: Path
    series: int = -1
    compression: str = "Uncompressed"
    big_tiff: bool = False
    overwrite: bool = False
    max_heap: str | None = None
    extra_classpath: list[Path] = field(default_factory=list)


# --------------------------------------------------------------------------- #
#  A run
# --------------------------------------------------------------------------- #

class Conversion:
    """One conversion, one JVM.

        conversion = Conversion(Options(input, output)).start()
        while not isinstance(event := conversion.events.get(), Finished):
            ...

    The JVM's output is read on a thread of its own and arrives on `events`, a
    queue that ends with exactly one `Finished`. A queue rather than a
    generator, so that the reader can wait on it with a timeout - which is what
    lets Ctrl+C through on Windows, and what a Tk window polls.

    `stop()` may be called from any thread: it asks the command's Task to
    cancel, and the conversion ends before its next plane with a `Finished`
    whose status is "stopped".

    The thread first fetches any reader jar not cached yet, so a first run's
    download never blocks the caller - a window in particular.
    """

    def __init__(self, options: Options) -> None:
        self.options = options
        self.process: subprocess.Popen | None = None
        self.events: queue.Queue[Event] = queue.Queue()
        #: Asked for before the JVM existed; acted on as soon as it does.
        self._stop_requested = False
        self._killed = False
        self._lock = threading.Lock()

    def start(self) -> Conversion:
        jgo_command()  # a missing jgo is the caller's error, not the thread's
        threading.Thread(target=self._run, name="conversion", daemon=True).start()
        return self

    def argv(self, downloaded: list[Path]) -> list[str]:
        o = self.options
        return _jgo_run(
            max_heap=o.max_heap, extra_classpath=[*downloaded, *o.extra_classpath],
            groovy_args=[str(SHIM), "--watch-stdin", str(o.input), str(o.output),
                         str(o.series), o.compression,
                         str(o.big_tiff).lower(), str(o.overwrite).lower()],
        )

    def _run(self) -> None:
        try:
            downloaded = fetch_jars(lambda line: self.events.put(Output(line)))
            with self._lock:
                if self._killed:
                    self.events.put(Finished("stopped", "Stopped before it began"))
                    return
                self.process = _popen(self.argv(downloaded))
                if self._stop_requested:
                    self._write_stop()
        except Exception as error:  # noqa: BLE001 -- reported, not lost on a thread
            self.events.put(Finished("failed", f"could not start the converter: "
                                               f"{error}"))
            return
        self._read()

    def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        finished: Finished | None = None
        tail: deque[str] = deque(maxlen=TAIL_LINES)
        try:
            for raw in self.process.stdout:
                line = raw.rstrip("\r\n")
                kind, _, rest = line.partition(" ")
                if kind == "B2I_PROGRESS":
                    done, total, message = (rest.split(" ", 2) + [""])[:3]
                    self.events.put(Progress(int(done), int(total), message))
                elif kind == "B2I_DONE":
                    finished = Finished("done", f"{rest} planes written",
                                        planes=int(rest))
                elif kind == "B2I_STOPPED":
                    finished = Finished("stopped", rest)
                elif kind == "B2I_FAILED":
                    finished = Finished("failed", rest)
                elif not line.startswith(JAVA_TOOL_OPTIONS_ECHO):
                    tail.append(line)
                    self.events.put(Output(line))
        finally:
            returncode = self.process.wait()
            if finished is None:
                finished = Finished(
                    "failed", f"the JVM ended without a result (exit {returncode})")
            finished.returncode = returncode
            finished.tail = list(tail)
            self.events.put(finished)

    def stop(self) -> None:
        """Ask for a stop between two planes; the run reports it when it lands."""
        with self._lock:
            self._stop_requested = True
            self._write_stop()

    def _write_stop(self) -> None:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            return
        try:
            process.stdin.write("stop\n")
            process.stdin.flush()
        except OSError:
            pass  # it is already on its way out

    def kill(self) -> None:
        """Stop now, mid-plane. jgo is a parent of the JVM, so the tree goes."""
        with self._lock:
            self._killed = True
            process = self.process
        if process is None or process.poll() is not None:
            return
        import psutil

        try:
            parent = psutil.Process(process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass


def prepare() -> Iterator[str]:
    """Fetch everything a first run would, and check the readers are picked up.

    The first run on a machine downloads a JDK, the converter's jars and the
    reader jars. Doing it here, as its own step, keeps that wait from looking
    like a conversion that hangs. The JVM is then asked which of the external
    readers Bio-Formats actually instantiated, on the same class path a
    conversion gets.

    The last line yielded is `READY <java version>; <readers>`; a JVM that does
    not start raises.
    """
    found: list[str] = []
    if shared():
        fetch_shared_jdk(found.append)
    downloaded = fetch_jars(found.append)
    yield from found
    labels = {reader.reader_class: reader.label for reader in EXTERNAL_READERS}
    process = _popen(_jgo_run(max_heap=None, extra_classpath=downloaded,
                              groovy_args=[str(PROBE), *labels]))
    java, loaded, missing = None, [], []
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.rstrip("\r\n")
        if line.startswith("B2I_JAVA "):
            java = line[len("B2I_JAVA "):]
        elif line.startswith("B2I_READER "):
            reader_class, _, state = line[len("B2I_READER "):].rpartition(" ")
            (loaded if state == "loaded" else missing).append(labels[reader_class])
            yield f"  {labels[reader_class]} reader: {state}"
        elif not line.startswith(JAVA_TOOL_OPTIONS_ECHO):
            yield line
    returncode = process.wait()
    if java is None:
        raise RuntimeError(f"jgo could not start the converter (exit {returncode})")
    readers = (f"MISSING readers: {', '.join(missing)}" if missing
               else f"{len(loaded)} extra readers loaded")
    yield f"READY {java}; {readers}"
