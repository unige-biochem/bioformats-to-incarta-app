"""A window over the converter, for someone who should not need a terminal.

    bioformats-to-incarta-gui
    Start.cmd / start.sh          (from a checkout: double-click)

It fills in the same options the command line takes and runs the same
`launcher.Conversion`, in-process: the JVM's output is read on a thread and
lands on a queue the window drains every 100 ms, because Tk must only be
touched from its own thread. One conversion at a time.

What it remembers - the last folders and options - is kept in a settings file
in the user's own configuration folder, never next to the code.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__, launcher
from .launcher import Conversion, Finished, Options, Output, Progress

TITLE = "Bio-Formats to IN Carta"
GREY = "#666666"
MONO = ("Consolas" if os.name == "nt" else "Menlo", 9)
POLL_MS = 100
#: Lines kept in the log pane; a long conversion must not grow it forever.
LOG_LINES = 5000
#: The Biochemistry and UNIGE / ACCESS Geneva logos, already at header height
#: for each display scale (Tk cannot resize an image smoothly), and the window
#: icon in a few sizes. Tk reads PNG itself; no imaging library needed.
ASSETS = Path(__file__).resolve().parent / "assets"
LOGO_SCALES = (100, 125, 150, 175, 200, 250, 300)
ICON_SIZES = (16, 32, 48, 256)


def settings_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "bioformats-to-incarta-app" / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass  # forgetting the last folder is not worth an error dialog


def load_image(name: str) -> tk.PhotoImage | None:
    try:
        return tk.PhotoImage(file=str(ASSETS / name))
    except tk.TclError:
        return None  # a missing logo is not worth refusing to start


def logo_scale(root: tk.Tk) -> int:
    """The prepared logo scale closest to the display's, 96 dpi being 100 %."""
    percent = root.winfo_fpixels("1i") / 96 * 100
    return min(LOGO_SCALES, key=lambda s: abs(s - percent))


def sharp_on_windows() -> None:
    """Draw at the display's real resolution instead of letting Windows stretch
    a 96 dpi window, which blurs text and logos on any scaled display."""
    if os.name != "nt":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Windows 8.1 and later
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 -- a folder the user chose
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App:

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.conversion: Conversion | None = None
        self.busy = False
        self.stopping = False
        self.lines: queue.Queue[str | None] = queue.Queue()  # from prepare()
        settings = load_settings()

        root.title(TITLE)
        #: Kept on self: Tk drops an image Python no longer references.
        self.icons = [i for i in (load_image(f"icon-{n}.png") for n in ICON_SIZES) if i]
        if self.icons:
            root.iconphoto(True, *self.icons)
        root.minsize(640, 520)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.input = tk.StringVar(value=settings.get("input", ""))
        self.output = tk.StringVar(value=settings.get("output", ""))
        self.series = tk.StringVar(value=str(settings.get("series", -1)))
        self.compression = tk.StringVar(value=settings.get("compression", "Uncompressed"))
        if self.compression.get() not in launcher.COMPRESSIONS:
            self.compression.set("Uncompressed")
        self.big_tiff = tk.BooleanVar(value=settings.get("big_tiff", False))
        self.overwrite = tk.BooleanVar(value=settings.get("overwrite", False))
        self.memory = tk.StringVar(value=settings.get("memory", ""))
        self.status = tk.StringVar(value="")
        #: The output folder we suggested, so choosing another input updates it
        #: unless the user typed their own.
        self.suggested_output = ""

        self._build()

    # -- layout ------------------------------------------------------------ #

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        self.header = ttk.Frame(outer)
        self.header.pack(fill="x")
        self.logos = load_image(f"logos-{logo_scale(self.root)}.png")
        if self.logos:  # packed first so the title takes what is left
            ttk.Label(self.header, image=self.logos).pack(side="right", anchor="n",
                                                          padx=(16, 0))
        ttk.Label(self.header, text=TITLE, font=("TkDefaultFont", 14, "bold")).pack(
            anchor="w")
        ttk.Label(self.header, foreground=GREY,
                  text="Converts any file Bio-Formats can open into a dataset "
                       "IN Carta can import.").pack(anchor="w", pady=(2, 0))

        form = ttk.Frame(outer)
        form.pack(fill="x", pady=(14, 0))
        form.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(form, text="Input image").grid(row=row, column=0, sticky="w",
                                                 padx=(0, 10), pady=3)
        ttk.Entry(form, textvariable=self.input).grid(row=row, column=1, sticky="ew",
                                                      pady=3)
        ttk.Button(form, text="Browse…", command=self.pick_input).grid(
            row=row, column=2, padx=(6, 0), pady=3)

        row += 1
        ttk.Label(form, text="Output folder").grid(row=row, column=0, sticky="w",
                                                   padx=(0, 10), pady=3)
        ttk.Entry(form, textvariable=self.output).grid(row=row, column=1,
                                                       sticky="ew", pady=3)
        ttk.Button(form, text="Browse…", command=self.pick_output).grid(
            row=row, column=2, padx=(6, 0), pady=3)

        row += 1
        options = ttk.Frame(form)
        options.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(options, text="Series").pack(side="left")
        ttk.Spinbox(options, from_=-1, to=9999, width=6,
                    textvariable=self.series).pack(side="left", padx=(6, 0))
        ttk.Label(options, text="(-1 = all)", foreground=GREY).pack(side="left",
                                                                    padx=(4, 18))
        ttk.Label(options, text="Compression").pack(side="left")
        ttk.Combobox(options, textvariable=self.compression, state="readonly",
                     values=launcher.COMPRESSIONS, width=16).pack(side="left",
                                                                  padx=(6, 0))

        row += 1
        checks = ttk.Frame(form)
        checks.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(checks, text="BigTIFF (planes above 4 GB)",
                        variable=self.big_tiff).pack(side="left")
        ttk.Checkbutton(checks, text="Overwrite existing files",
                        variable=self.overwrite).pack(side="left", padx=(18, 0))
        ttk.Label(checks, text="Java memory").pack(side="left", padx=(18, 0))
        ttk.Entry(checks, textvariable=self.memory, width=6).pack(side="left",
                                                                  padx=(6, 0))
        ttk.Label(checks, text="(e.g. 8G; empty = automatic)",
                  foreground=GREY).pack(side="left", padx=(4, 0))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(16, 0))
        self.convert_button = ttk.Button(buttons, text="Convert", command=self.convert)
        self.convert_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop", command=self.stop,
                                      state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Open output folder",
                   command=self.open_output).pack(side="left", padx=(8, 0))
        self.prepare_button = ttk.Button(buttons, text="Check Java setup",
                                         command=self.prepare)
        self.prepare_button.pack(side="right")

        self.progress = ttk.Progressbar(outer, mode="determinate")
        self.progress.pack(fill="x", pady=(12, 0))
        ttk.Label(outer, textvariable=self.status, foreground=GREY).pack(
            anchor="w", pady=(4, 0))

        self.log = ScrolledText(outer, height=12, font=MONO, wrap="none",
                                state="disabled")
        self.log.pack(fill="both", expand=True, pady=(8, 0))
        ttk.Label(outer, foreground=GREY,
                  text=f"bioformats-to-incarta-app {__version__}  ·  "
                       f"{launcher.CONVERTER}").pack(anchor="e", pady=(6, 0))

    # -- small actions ----------------------------------------------------- #

    def pick_input(self) -> None:
        current = Path(self.input.get())
        chosen = filedialog.askopenfilename(
            title="Choose the image to convert",
            initialdir=str(current.parent) if current.parent.is_dir() else None)
        if not chosen:
            return
        self.input.set(str(Path(chosen)))
        if not self.output.get() or self.output.get() == self.suggested_output:
            source = Path(chosen)
            self.suggested_output = str(source.parent / f"{source.stem}_incarta")
            self.output.set(self.suggested_output)

    def pick_output(self) -> None:
        current = Path(self.output.get())
        chosen = filedialog.askdirectory(
            title="Choose where the converted dataset goes",
            initialdir=str(current) if current.is_dir() else None)
        if chosen:
            self.output.set(str(Path(chosen)))

    def open_output(self) -> None:
        folder = Path(self.output.get())
        if folder.is_dir():
            open_folder(folder)
        else:
            messagebox.showinfo(TITLE, "The output folder does not exist yet.")

    def write(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        excess = int(self.log.index("end-1c").split(".")[0]) - LOG_LINES
        if excess > 0:
            self.log.delete("1.0", f"{excess + 1}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.convert_button.configure(state="disabled" if busy else "normal")
        self.prepare_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy and self.conversion
                                   else "disabled", text="Stop")

    def remember(self) -> None:
        save_settings({
            "input": self.input.get(), "output": self.output.get(),
            "series": self.series.get(), "compression": self.compression.get(),
            "big_tiff": self.big_tiff.get(), "overwrite": self.overwrite.get(),
            "memory": self.memory.get(),
        })

    # -- converting -------------------------------------------------------- #

    def options(self) -> Options | None:
        source = Path(self.input.get().strip())
        target = self.output.get().strip()
        problem = None
        if not source.is_file():
            problem = "Choose an input image that exists."
        elif not target:
            problem = "Choose an output folder."
        elif Path(target).exists() and not Path(target).is_dir():
            problem = "The output path exists and is not a folder."
        try:
            series = int(self.series.get())
            if series < -1:
                raise ValueError
        except ValueError:
            problem = problem or "Series is -1 (all) or a series index from 0."
            series = -1
        if problem:
            messagebox.showwarning(TITLE, problem)
            return None
        return Options(input=source.resolve(), output=Path(target).resolve(),
                       series=series, compression=self.compression.get(),
                       big_tiff=self.big_tiff.get(), overwrite=self.overwrite.get(),
                       max_heap=self.memory.get().strip() or None)

    def convert(self) -> None:
        options = self.options()
        if options is None:
            return
        self.remember()
        self.stopping = False
        self.progress.configure(value=0, maximum=1)
        self.write(f"\n=== {options.input.name}  ->  {options.output}")
        self.status.set("Starting Java…")
        try:
            self.conversion = Conversion(options).start()
        except (OSError, RuntimeError) as error:
            self.conversion = None
            self.status.set("")
            messagebox.showerror(TITLE, str(error))
            return
        self.set_busy(True)
        self.root.after(POLL_MS, self.poll)

    def poll(self) -> None:
        conversion = self.conversion
        if conversion is None:
            return
        while True:
            try:
                event = conversion.events.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, Progress):
                self.progress.configure(maximum=max(event.total, 1), value=event.done)
                if not self.stopping:
                    self.status.set(f"{event.done} / {event.total} planes   "
                                    f"{event.message}")
            elif isinstance(event, Output):
                self.write(event.line)
            elif isinstance(event, Finished):
                self.finished(event)
                return
        self.root.after(POLL_MS, self.poll)

    def finished(self, event: Finished) -> None:
        self.conversion = None
        self.set_busy(False)
        output = self.output.get()
        if event.status == "done":
            self.status.set(f"Done: {event.message}.")
            self.write(f"=== Done: {event.message}.")
        elif event.status == "stopped" or self.stopping:
            self.status.set("Stopped. The output folder holds a partial dataset.")
            self.write(f"=== Stopped: {event.message}. {output} holds a partial "
                       f"dataset, without its .xdce index.")
        else:
            self.status.set("Failed - see the log.")
            self.write(f"=== Failed: {event.message}")
            messagebox.showerror(TITLE, f"The conversion failed:\n\n{event.message}")

    def stop(self) -> None:
        if self.conversion is None:
            return
        if not self.stopping:
            self.stopping = True
            self.conversion.stop()
            self.status.set("Stopping after the current plane… "
                            "(click Kill to stop at once)")
            self.stop_button.configure(text="Kill")
        else:
            self.conversion.kill()

    # -- preparing --------------------------------------------------------- #

    def prepare(self) -> None:
        """Fetch Java and the converter now, rather than inside a first run."""
        self.set_busy(True)
        self.status.set("Fetching Java and the converter - "
                        "only slow the very first time…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        self.write(f"\n=== Checking Java and {launcher.CONVERTER}")

        def work() -> None:
            try:
                for line in launcher.prepare():
                    self.lines.put(line)
            except (OSError, RuntimeError) as error:
                self.lines.put(f"ERROR {error}")
            self.lines.put(None)

        threading.Thread(target=work, name="prepare", daemon=True).start()
        self.root.after(POLL_MS, self.poll_prepare)

    def poll_prepare(self) -> None:
        while True:
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                self.root.after(POLL_MS, self.poll_prepare)
                return
            if line is None:
                break
            if line.startswith("READY "):
                self.status.set(f"Ready: Java {line[len('READY '):]}")
                self.write(f"=== Ready: Java {line[len('READY '):]}")
            elif line.startswith("ERROR "):
                self.status.set("Java setup failed - see the log.")
                self.write(f"=== {line}")
            else:
                self.write(line)
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.set_busy(False)

    # -- closing ----------------------------------------------------------- #

    def on_close(self) -> None:
        if self.conversion is not None:
            if not messagebox.askyesno(
                    TITLE, "A conversion is running. Stop it and close?"):
                return
            self.conversion.kill()
        self.remember()
        self.root.destroy()


def main() -> None:
    sharp_on_windows()  # before the first window exists
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
