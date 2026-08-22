import pytest

from labnexus_pyprobe.cli import build_parser, build_settings, resolve_ui


def parse(*argv):
    parser = build_parser()
    args = parser.parse_args(list(argv))
    return parser, args


def test_positional_args_still_work(tmp_path):
    parser, args = parse(str(tmp_path), "lab.example.com:8000")
    settings = build_settings(args, parser, "plain")
    assert settings.directory == tmp_path
    assert settings.server == "http://lab.example.com:8000"


def test_flags_override_positionals(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    parser, args = parse(str(tmp_path), "a.example", "-d", str(other), "-s", "b.example")
    settings = build_settings(args, parser, "plain")
    assert settings.directory == other
    assert settings.server == "http://b.example"


def test_env_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("LABNEXUS_DIR", str(tmp_path))
    monkeypatch.setenv("LABNEXUS_SERVER", "env.example:9000")
    parser, args = parse()
    settings = build_settings(args, parser, "plain")
    assert settings.directory == tmp_path
    assert settings.server == "http://env.example:9000"


def test_patterns_excludes_and_scheme(tmp_path):
    parser, args = parse(
        str(tmp_path), "lab.example", "-p", "*.csv", "-p", "*.txt",
        "-x", "draft_*", "--scheme", "https", "-r", "-i", "30",
    )
    settings = build_settings(args, parser, "plain")
    assert settings.patterns == ["*.csv", "*.txt"]
    assert "draft_*" in settings.excludes and ".*" in settings.excludes
    assert settings.server == "https://lab.example"
    assert settings.recursive and settings.interval == 30


def test_no_default_excludes(tmp_path):
    parser, args = parse(str(tmp_path), "lab.example", "--no-default-excludes", "-x", "*.bak")
    settings = build_settings(args, parser, "plain")
    assert settings.excludes == ["*.bak"]


def test_missing_directory_is_an_error():
    parser, args = parse("-s", "lab.example")
    with pytest.raises(SystemExit):
        build_settings(args, parser, "plain")


def test_gui_tolerates_missing_arguments():
    parser, args = parse()
    settings = build_settings(args, parser, "gui")  # must not raise
    assert settings.server == ""


def test_no_state_disables_history(tmp_path):
    parser, args = parse(str(tmp_path), "lab.example", "--no-state")
    assert build_settings(args, parser, "plain").state_file is None


@pytest.mark.parametrize(
    ("argv", "expected"),
    [(["--gui"], "gui"), (["--plain"], "plain"), (["--ui", "tui"], "tui")],
)
def test_resolve_ui(argv, expected, tmp_path):
    _, args = parse(str(tmp_path), "lab.example", *argv)
    assert resolve_ui(args) == expected
