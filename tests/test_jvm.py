"""End to end, through jgo and a real JVM, on Bio-Formats' `.fake` reader.

The first run on a machine downloads a JDK and the converter; skip these with
`pytest -m "not jvm"`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bioformats_to_incarta_app import cli, launcher
from bioformats_to_incarta_app.launcher import (Conversion, Finished, Options,
                                                Progress)

pytestmark = pytest.mark.jvm

PLATE = "plate&plates=1&plateRows=1&plateCols=2&fields=1&sizeX=32&sizeY=32&sizeC=2.fake"


def fake(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.touch()
    return path


def test_converts_a_plate(tmp_path):
    out = tmp_path / "out"
    assert cli.main([str(fake(tmp_path, PLATE)), str(out)]) == 0
    assert len(list(out.glob("*.tif"))) == 4
    assert len(list(out.glob("*.xdce"))) == 1


def test_a_refused_overwrite_fails(tmp_path):
    source, out = fake(tmp_path, PLATE), tmp_path / "out"
    assert cli.main([str(source), str(out)]) == 0
    assert cli.main([str(source), str(out)]) == 1
    assert cli.main([str(source), str(out), "--overwrite"]) == 0


def test_stops_between_planes(tmp_path):
    source = fake(tmp_path, "big&sizeX=512&sizeY=512&sizeZ=50&sizeC=2.fake")
    conversion = Conversion(Options(source, tmp_path / "out")).start()
    while not isinstance(event := conversion.events.get(timeout=120), Finished):
        if isinstance(event, Progress) and event.done == 3:
            conversion.stop()
    assert event.status == "stopped"
    written = len(list((tmp_path / "out").glob("*.tif")))
    assert 3 <= written < 100
    assert not list((tmp_path / "out").glob("*.xdce"))


def test_both_external_readers_are_picked_up():
    lines = list(launcher.prepare())
    assert lines[-1].startswith("READY 21.")
    assert "2 extra readers loaded" in lines[-1], lines
