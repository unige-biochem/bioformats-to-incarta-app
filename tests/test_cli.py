from __future__ import annotations

import pytest

from bioformats_to_incarta_app import cli


def test_input_must_exist(tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main([str(tmp_path / "missing.nd2"), str(tmp_path / "out")])
    assert stop.value.code == 2
    assert "is not a file" in capsys.readouterr().err


def test_output_must_not_be_a_file(tmp_path, capsys):
    source = tmp_path / "in.fake"
    source.touch()
    with pytest.raises(SystemExit):
        cli.main([str(source), str(source)])
    assert "not a folder" in capsys.readouterr().err


def test_compression_is_one_of_the_dialog_choices(tmp_path):
    source = tmp_path / "in.fake"
    source.touch()
    with pytest.raises(SystemExit):
        cli.main([str(source), str(tmp_path / "out"), "--compression", "zip"])
