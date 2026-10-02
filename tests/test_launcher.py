"""The protocol between the Groovy shim and Python, without starting a JVM."""

from __future__ import annotations

import io
from pathlib import Path

from bioformats_to_incarta_app import launcher
from bioformats_to_incarta_app.launcher import (Conversion, Finished, Options,
                                                Output, Progress)


class FakeProcess:
    def __init__(self, stdout: str, returncode: int) -> None:
        self.stdout = io.StringIO(stdout)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


def events_from(stdout: str, returncode: int = 0) -> list:
    conversion = Conversion(Options(Path("in.fake"), Path("out")))
    conversion.process = FakeProcess(stdout, returncode)
    conversion._read()
    events = []
    while not conversion.events.empty():
        events.append(conversion.events.get())
    return events


def test_progress_and_done_are_parsed():
    events = events_from(
        "Picked up JAVA_TOOL_OPTIONS: -Djava.awt.headless=true\n"
        "B2I_PROGRESS 0 2 t1_A01_s1_w1_z1.tif\n"
        "[INFO] something\n"
        "B2I_PROGRESS 2 2 done\n"
        "B2I_DONE 2\n")
    assert events[0] == Progress(0, 2, "t1_A01_s1_w1_z1.tif")
    assert events[1] == Output("[INFO] something")
    assert events[2] == Progress(2, 2, "done")
    assert isinstance(events[3], Finished)
    assert (events[3].status, events[3].planes) == ("done", 2)
    assert len(events) == 4  # the JAVA_TOOL_OPTIONS echo is dropped


def test_a_message_with_spaces_survives():
    events = events_from("B2I_PROGRESS 1 3 a name with spaces.tif\nB2I_DONE 3\n")
    assert events[0] == Progress(1, 3, "a name with spaces.tif")


def test_stopped_and_failed_are_reported():
    stopped = events_from("B2I_STOPPED Stopped on request\n", 3)[-1]
    assert (stopped.status, stopped.message, stopped.returncode) == (
        "stopped", "Stopped on request", 3)
    failed = events_from("Exception in thread\nB2I_FAILED no such file\n", 1)[-1]
    assert (failed.status, failed.message) == ("failed", "no such file")
    assert failed.tail == ["Exception in thread"]


def test_a_jvm_that_dies_silently_is_a_failure():
    finished = events_from("Error: could not find or load main class\n", 1)[-1]
    assert finished.status == "failed"
    assert "exit 1" in finished.message


def test_argv_reaches_the_shim_in_order(tmp_path):
    options = Options(tmp_path / "a b.nd2", tmp_path / "out", series=2,
                      compression="LZW", big_tiff=True, max_heap="4G",
                      extra_classpath=[tmp_path / "reader.jar"])
    argv = Conversion(options).argv([tmp_path / "SlideBook6Reader.jar"])
    assert argv[argv.index("--max-heap") + 1] == "4G"
    classpath = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--add-classpath"]
    assert classpath == [str(tmp_path / "SlideBook6Reader.jar"),
                         str(tmp_path / "reader.jar")]
    assert launcher.ENDPOINT in argv
    shim_args = argv[argv.index(str(launcher.SHIM)):]
    assert shim_args[1:] == ["--watch-stdin", str(tmp_path / "a b.nd2"),
                             str(tmp_path / "out"), "2", "LZW", "true", "false"]


def test_the_czi_reader_is_part_of_the_endpoint():
    assert "ch.epfl.biop:quick-start-czi-reader:0.3.0" in launcher.ENDPOINT.split("+")


def test_a_downloaded_reader_is_cached_under_its_pinned_version(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    slidebook = next(r for r in launcher.EXTERNAL_READERS if r.url)
    assert slidebook.jar.name == "SlideBook6Reader-20260903101721.jar"
    assert tmp_path in slidebook.jar.parents


def test_an_unreachable_reader_is_skipped_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    def offline(url, destination):
        raise OSError("offline")

    monkeypatch.setattr(launcher, "download", offline)
    said = []
    assert launcher.fetch_jars(said.append) == []
    assert any("WARNING" in line for line in said)


def shared_install(monkeypatch, tmp_path, exists: bool) -> Path:
    shared = tmp_path / "java"
    if exists:
        shared.mkdir()
    monkeypatch.setattr(launcher, "SHARED", shared)
    monkeypatch.setattr(launcher, "SHARED_JDK", shared / "jdk")
    return shared


def test_a_shared_install_keeps_everything_in_its_folder(monkeypatch, tmp_path):
    shared = shared_install(monkeypatch, tmp_path, exists=True)
    argv = Conversion(Options(tmp_path / "in.fake", tmp_path / "out")).argv([])
    jgo_args = argv[:argv.index("run")]
    assert jgo_args[jgo_args.index("--cache-dir") + 1] == str(shared / "jgo")
    assert jgo_args[jgo_args.index("--repo-cache") + 1] == str(shared / "m2")
    assert launcher.jar_cache() == shared / "jars"
    environment = launcher._environment()
    assert environment["JAVA_HOME"] == str(shared / "jdk")
    assert environment["PATH"].startswith(str(shared / "jdk" / "bin"))


def test_without_a_shared_install_the_user_caches_are_used(monkeypatch, tmp_path):
    shared_install(monkeypatch, tmp_path, exists=False)
    argv = Conversion(Options(tmp_path / "in.fake", tmp_path / "out")).argv([])
    assert "--cache-dir" not in argv and "--repo-cache" not in argv
    assert tmp_path not in launcher.jar_cache().parents
    assert launcher._environment().get("JAVA_HOME") != str(tmp_path / "java" / "jdk")
