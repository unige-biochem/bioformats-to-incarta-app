# Bio-Formats to IN Carta app

A command line and a small window around
[bioformats-to-incarta](https://github.com/unige-biochem/bioformats-to-incarta),
which converts any Bio-Formats readable file into a dataset IN Carta can import.

It runs the same SciJava command as Fiji's *Plugins > UNIGE > Bio-Formats to IN
Carta*, without Fiji: [jgo](https://github.com/scijava/jgo) resolves the
converter from maven.scijava.org and fetches a JDK, so the machine needs neither
Fiji nor Java. The first run downloads about 60 MB of jars plus a JDK; later runs
start in a few seconds.

## The window

Double-click `Start.cmd` (Windows) or run `./start.sh` (macOS, Linux). Both
install [uv](https://docs.astral.sh/uv/) if it is missing; uv then brings
Python and everything else.

On Windows, the first start also creates a **Bio-Formats to IN Carta**
shortcut next to `Start.cmd`, with the app's icon. Use it from then on, or copy
it to the Desktop. A `.cmd` file cannot carry an icon itself, and a shortcut
cannot ship in the zip because it stores absolute paths. So `Start.cmd`
rewrites the shortcut on every start, which keeps it valid if the folder moves.

Choose an image and an output folder, then **Convert**. **Stop** ends the
conversion before its next plane; a second click kills it at once. **Check Java
setup** does the first-run downloads up front, so they do not look like a
conversion that hangs.

## Installing for every user of a Windows machine

Double-click `Install.cmd`; it asks for administrator rights. It installs into
`C:\Program Files\Bio-Formats to IN Carta` (or the folder given as its
argument) and puts a **Bio-Formats to IN Carta** shortcut in the Start Menu
and on the Desktop of every user.

That folder holds everything: the app, its own Python, a JDK and the
converter's jars, all downloaded during the install (about 500 MB). Users can
read the folder but not write to it, so they have nothing to download, and no
uv or Java of their own is needed. The app recognizes such an install
by its `java` folder and uses only that folder's caches; each user still keeps
their own settings.

To update, run the new version's `Install.cmd` again. Do the same if a new
version needs jars the folder does not have yet: users cannot add them. To
uninstall, delete the folder and the two shortcuts.

The command line of an installed copy is
`"C:\Program Files\Bio-Formats to IN Carta\.venv\Scripts\bioformats-to-incarta.exe"`.

## The command line

```bash
uv run bioformats-to-incarta plate.nd2 converted/
uv run bioformats-to-incarta plate.lif converted/ --series 2 --compression LZW
uv run bioformats-to-incarta --prepare        # only fetch Java and the converter
```

| Option | Meaning |
|---|---|
| `--series N` | Series to convert; `-1` (default) converts them all |
| `--compression` | `Uncompressed` (default), `LZW`, `JPEG-2000`, `JPEG-2000 Lossy`, `zlib` |
| `--bigtiff` | Needed for planes above the 4 GB TIFF limit |
| `--overwrite` | Replace files already in the output folder |
| `--memory 8G` | Maximum Java heap; jgo chooses one otherwise |
| `-v` | Show the libraries' own output as it comes |

Ctrl+C stops before the next plane; a second Ctrl+C kills. Exit codes: `0`
converted, `1` failed, `2` bad arguments, `3` stopped. A stopped conversion
leaves its planes but no `.xdce` index, so it cannot be imported by mistake.

## How it works

```
cli.py / gui.py  ->  launcher.Conversion  ->  jgo  ->  JVM: run_command.groovy
                                                         -> CommandService
                                                         -> BioformatsToIncartaCommand
```

- **One endpoint**: `ch.unige.biochem:bioformats-to-incarta` plus Groovy,
  logback (otherwise Bio-Formats' warnings go nowhere) and the CZI reader below,
  all pinned in `launcher.py`. The converter depends on SciJava and
  Bio-Formats only, so there is no ImageJ in it.
- **The Groovy shim** builds a minimal SciJava context, runs the command
  through the `CommandService`, and prints one `B2I_*` line per event:
  progress, done, stopped, failed. Progress comes from the command's
  `Task` (`TaskService`). A `stop` line on the JVM's stdin cancels that task.
- **Java 21** is pinned, which is what Fiji ships. jgo 3.1 drops JVM arguments
  given after its `--`, so headless mode and UTF-8 output are set through
  `JAVA_TOOL_OPTIONS`.

### Extra readers

Two readers that do better than Bio-Formats' own are always on the class path:

| Format | Reader | Comes from |
|---|---|---|
| Zeiss `.czi` | `ch.epfl.biop:quick-start-czi-reader:0.3.0` | maven.scijava.org, as part of the endpoint |
| SlideBook `.sld` | `SlideBook6Reader.jar` (native libraries included) | the [SlideBook update site](https://sites.imagej.net/SlideBook/), downloaded once into `%LOCALAPPDATA%\bioformats-to-incarta-app\jars` (`~/.cache/...` elsewhere), never into the repository |

No registration is needed. Bio-Formats' own `readers.txt` already lists both
(`[type=external]`) ahead of the built-in reader they replace, and uses them
whenever they are on the class path. `--prepare` / **Check Java setup** asks the
JVM which readers Bio-Formats actually loaded and reports both. If the SlideBook
download fails, the conversion still runs and Bio-Formats falls back to its
built-in reader, which is much slower on large files.

Any other jar can be added from the command line with `--add-jar PATH`.

The window remembers its last folders and options in
`%APPDATA%\bioformats-to-incarta-app\settings.json` (`~/.config/...` elsewhere).

## Development

```bash
uv sync
uv run pytest                  # includes real JVM runs on Bio-Formats .fake files
uv run pytest -m "not jvm"     # protocol and argument tests only
```

The icon (Bio-Formats layers → well plate) is generated from OME's vector logo,
which the script downloads itself:

```bash
uv run --no-project --with pymupdf --with pillow python tools/make_icon.py
```

## License

MIT, see [LICENSE.txt](LICENSE.txt). Bio-Formats' full format support
(`ome:formats-gpl`), which the converter downloads at run time, is GPL licensed.
