from pathlib import Path

import pytest

from labnexus_pyprobe.config import Settings, normalise_server


@pytest.mark.parametrize(
    ("raw", "scheme", "expected"),
    [
        ("lab.example.com:8000", "http", "http://lab.example.com:8000"),
        ("lab.example.com", "https", "https://lab.example.com"),
        ("https://lab.example.com/", "http", "https://lab.example.com"),
        ("  lab.example.com  ", "http", "http://lab.example.com"),
    ],
)
def test_normalise_server(raw, scheme, expected):
    assert normalise_server(raw, scheme) == expected


def test_patterns_and_excludes(tmp_path: Path):
    settings = Settings(directory=tmp_path, server="http://x", patterns=["*.csv"])
    assert settings.matches(tmp_path / "run.csv")
    assert not settings.matches(tmp_path / "run.txt")
    assert not settings.matches(tmp_path / ".hidden.csv")
    assert not settings.matches(tmp_path / "run.csv.part")


def test_iter_candidates_respects_recursion(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.csv").write_text("a")
    (tmp_path / "sub" / "deep.csv").write_text("b")

    flat = Settings(directory=tmp_path, server="http://x")
    assert [p.name for p in flat.iter_candidates()] == ["top.csv"]

    deep = Settings(directory=tmp_path, server="http://x", recursive=True)
    assert sorted(p.name for p in deep.iter_candidates()) == ["deep.csv", "top.csv"]


def test_state_file_inside_watched_dir_is_never_uploaded(tmp_path: Path):
    state = tmp_path / "uploads.json"
    state.write_text("{}")
    settings = Settings(directory=tmp_path, server="http://x", state_file=state)
    assert not settings.matches(state)
    assert settings.iter_candidates() == []
