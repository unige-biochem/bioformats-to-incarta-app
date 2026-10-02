"""Bio-Formats to IN Carta, without Fiji: a command line and a window over jgo."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bioformats-to-incarta-app")
except PackageNotFoundError:  # running from a source tree that was not installed
    __version__ = "unknown"
