from pathlib import Path

import pytest
from labnexus_plate_parsers import SpectrometerModel

from labnexus_pyprobe import cli
from labnexus_pyprobe.cli import build_parser, build_settings, resolve_ui


def parse(*argv):
    parser = build_parser()
    args = parser.parse_args(list(argv))
    return parser, args


def test_positional_args_still_work(tmp_path):
    parser, args = parse(str(tmp_path), "lab.example.com:8000")
    settings = build_settings(args, parser, "plain")
    assert settings.queues[0].directory == tmp_path
    assert settings.server == "https://lab.example.com:8000"


def test_flags_override_positionals(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    parser, args = parse(str(tmp_path), "a.example", "-d", str(other), "-s", "b.example")
    settings = build_settings(args, parser, "plain")
    assert settings.queues[0].directory == other
    assert settings.server == "https://b.example"


def test_env_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("LABNEXUS_DIR", str(tmp_path))
    monkeypatch.setenv("LABNEXUS_SERVER", "env.example:9000")
    parser, args = parse()
    settings = build_settings(args, parser, "plain")
    assert settings.queues[0].directory == tmp_path
    assert settings.server == "https://env.example:9000"


def test_plain_http_is_refused_by_default(tmp_path):
    parser, args = parse(str(tmp_path), "lab.example.com:8000", "--scheme", "http")
    with pytest.raises(SystemExit):
        build_settings(args, parser, "plain")


def test_https_override_allows_plain_http(tmp_path):
    parser, args = parse(
        str(tmp_path), "lab.example.com:8000", "--scheme", "http", "--https-override", "true",
    )
    settings = build_settings(args, parser, "plain")
    assert settings.server == "http://lab.example.com:8000"


def test_patterns_excludes_and_scheme(tmp_path):
    parser, args = parse(
        str(tmp_path), "lab.example", "-p", "*.csv", "-p", "*.txt",
        "-x", "draft_*", "--scheme", "https", "-r", "-i", "30",
    )
    settings = build_settings(args, parser, "plain")
    assert settings.queues[0].effective_patterns == ["*.csv", "*.txt"]
    assert "draft_*" in settings.queues[0].excludes and ".*" in settings.queues[0].excludes
    assert settings.server == "https://lab.example"
    assert settings.queues[0].recursive and settings.interval == 30


def test_no_default_excludes(tmp_path):
    parser, args = parse(str(tmp_path), "lab.example", "--no-default-excludes", "-x", "*.bak")
    settings = build_settings(args, parser, "plain")
    assert settings.queues[0].excludes == ["*.bak"]


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


def test_bare_invocation_opens_the_gui(monkeypatch):
    monkeypatch.setattr(cli, "gui_available", lambda: True)
    _, args = parse()
    assert resolve_ui(args, bare=True) == "gui"


def test_bare_invocation_falls_back_without_a_display(monkeypatch):
    monkeypatch.setattr(cli, "gui_available", lambda: False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False, raising=False)
    _, args = parse()
    assert resolve_ui(args, bare=True) == "plain"


def test_arguments_keep_the_terminal_front_end(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "gui_available", lambda: True)
    _, args = parse(str(tmp_path), "lab.example", "--plain")
    assert resolve_ui(args, bare=False) == "plain"


def test_explicit_ui_choice_beats_the_bare_default(monkeypatch):
    monkeypatch.setattr(cli, "gui_available", lambda: True)
    _, args = parse("--ui", "plain")
    assert resolve_ui(args, bare=True) == "plain"


def test_main_with_no_args_launches_the_gui(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "gui_available", lambda: True)
    monkeypatch.setenv("LABNEXUS_DIR", str(tmp_path))
    monkeypatch.setenv("LABNEXUS_SERVER", "env.example:9000")
    monkeypatch.delenv("LABNEXUS_EMAIL", raising=False)

    seen = {}

    def fake_run_gui(settings, email="", version="", scheme="https", https_override=False):
        seen.update(settings=settings, email=email, scheme=scheme)
        return 0

    monkeypatch.setattr("labnexus_pyprobe.gui.run_gui", fake_run_gui)
    assert cli.main([]) == 0
    assert seen["settings"].queues[0].directory == tmp_path
    assert seen["settings"].server == "https://env.example:9000"
    assert seen["scheme"] == "https"


def test_gui_starts_with_blank_server_and_cwd(monkeypatch, tmp_path):
    """With nothing configured at all the window still opens, ready to be filled in."""
    monkeypatch.delenv("LABNEXUS_DIR", raising=False)
    monkeypatch.delenv("LABNEXUS_SERVER", raising=False)
    monkeypatch.chdir(tmp_path)
    parser, args = parse()
    settings = build_settings(args, parser, "gui")
    assert settings.server == ""
    assert settings.queues[0].directory == Path.cwd()


class TestSpectrometerQueues:
    def test_queue_flag_pairs_a_folder_with_an_instrument(self, tmp_path):
        spark = tmp_path / "spark"
        spark.mkdir()
        parser, args = parse("-s", "lab.example", "-w", "WS", "-Q", f"{spark}=tecan-spark")
        settings = build_settings(args, parser, "plain")

        assert len(settings.queues) == 1
        assert settings.queues[0].directory == spark
        assert settings.queues[0].model is SpectrometerModel.tecanSpark
        assert settings.queues[0].effective_patterns == ["*.xlsx", "*.xls"]

    def test_several_queues_keep_their_own_instruments(self, tmp_path):
        spark, epoch = tmp_path / "spark", tmp_path / "epoch"
        spark.mkdir()
        epoch.mkdir()
        parser, args = parse(
            "-s", "lab.example", "-w", "WS",
            "-Q", f"{spark}=tecanSpark",
            "-Q", f"{epoch}=Agilent Biotek Epoch 2",
        )
        settings = build_settings(args, parser, "plain")
        assert [q.model for q in settings.queues] == [
            SpectrometerModel.tecanSpark,
            SpectrometerModel.biotekEpoch2,
        ]

    def test_a_queue_without_a_model_is_a_plain_folder(self, tmp_path):
        parser, args = parse("-s", "lab.example", "-Q", str(tmp_path))
        settings = build_settings(args, parser, "plain")
        assert settings.queues[0].model is None
        assert settings.queues[0].effective_patterns == ["*"]

    def test_spectrometer_flag_applies_to_the_positional_folder(self, tmp_path):
        parser, args = parse(str(tmp_path), "lab.example", "-m", "spectramax-190", "-w", "WS")
        settings = build_settings(args, parser, "plain")
        assert settings.queues[0].model is SpectrometerModel.spectraMax190

    def test_queues_and_a_plain_directory_coexist(self, tmp_path):
        spark, misc = tmp_path / "spark", tmp_path / "misc"
        spark.mkdir()
        misc.mkdir()
        parser, args = parse("-s", "lab.example", "-w", "WS", "-Q", f"{spark}=tecanSpark",
                             "-d", str(misc))
        settings = build_settings(args, parser, "plain")
        assert [q.model for q in settings.queues] == [SpectrometerModel.tecanSpark, None]

    def test_unknown_model_is_an_error(self, tmp_path):
        parser, args = parse("-s", "lab.example", "-Q", f"{tmp_path}=Hitachi U-2000")
        with pytest.raises(SystemExit):
            build_settings(args, parser, "plain")

    def test_a_spectrometer_queue_without_a_workspace_is_an_error(self, tmp_path):
        parser, args = parse("-s", "lab.example", "-Q", f"{tmp_path}=tecanSpark")
        with pytest.raises(SystemExit):
            build_settings(args, parser, "plain")

    def test_a_plain_queue_needs_no_workspace(self, tmp_path):
        parser, args = parse("-s", "lab.example", "-Q", str(tmp_path))
        assert build_settings(args, parser, "plain").workspace_id is None

    def test_workspace_falls_back_to_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABNEXUS_WORKSPACE", "WS-FROM-ENV")
        parser, args = parse("-s", "lab.example", "-Q", f"{tmp_path}=tecanSpark")
        assert build_settings(args, parser, "plain").workspace_id == "WS-FROM-ENV"

    def test_spectrometer_falls_back_to_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABNEXUS_SPECTROMETER", "tecan-magellan")
        parser, args = parse(str(tmp_path), "lab.example", "-w", "WS")
        settings = build_settings(args, parser, "plain")
        assert settings.queues[0].model is SpectrometerModel.tecanMagellan

    def test_a_missing_queue_folder_is_an_error(self, tmp_path):
        parser, args = parse("-s", "lab.example", "-w", "WS",
                             "-Q", f"{tmp_path / 'nope'}=tecanSpark")
        with pytest.raises(SystemExit):
            build_settings(args, parser, "plain")

    def test_list_models_needs_no_server(self, capsys):
        assert cli.main(["--list-models"]) == 0
        out = capsys.readouterr().out
        assert "Tecan Spark" in out and "*.xlsx" in out
