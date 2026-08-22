import pytest

from labnexus_pyprobe.client import AuthError, LabNexusClient
from labnexus_pyprobe.config import Settings, normalise_server
from labnexus_pyprobe.watcher import Watcher


def make(server, tmp_path, **kwargs):
    settings = Settings(
        directory=tmp_path,
        server=normalise_server(server),
        min_age=0,
        state_file=tmp_path / "state.json",
        **kwargs,
    )
    client = LabNexusClient(settings.server)
    client.login("me@lab.org", "hunter2")
    events = []
    return Watcher(settings, client, on_event=events.append), events


def test_login_rejects_bad_credentials(server):
    client = LabNexusClient(normalise_server(server))
    with pytest.raises(AuthError):
        client.login("me@lab.org", "wrong")


def test_uploads_new_files_once(server, tmp_path, uploads):
    (tmp_path / "run.csv").write_text("alpha")
    watcher, events = make(server, tmp_path)

    watcher.scan_once()
    watcher.scan_once()

    assert watcher.stats.uploaded == 1
    assert len(uploads) == 1
    assert b"alpha" in uploads[0]
    assert [e.kind for e in events].count("uploaded") == 1


def test_reuploads_when_contents_change(server, tmp_path, uploads):
    target = tmp_path / "run.csv"
    target.write_text("alpha")
    watcher, _ = make(server, tmp_path)
    watcher.scan_once()

    target.write_text("beta")
    watcher.scan_once()

    assert watcher.stats.uploaded == 2
    assert b"beta" in uploads[-1]


def test_ignores_changes_when_reupload_disabled(server, tmp_path):
    target = tmp_path / "run.csv"
    target.write_text("alpha")
    watcher, _ = make(server, tmp_path, reupload_changed=False)
    watcher.scan_once()
    target.write_text("beta")
    watcher.scan_once()
    assert watcher.stats.uploaded == 1


def test_pattern_filtering(server, tmp_path, uploads):
    (tmp_path / "keep.csv").write_text("a")
    (tmp_path / "drop.log").write_text("b")
    watcher, _ = make(server, tmp_path, patterns=["*.csv"])
    watcher.scan_once()
    assert watcher.stats.uploaded == 1


def test_only_new_skips_pre_existing_files(server, tmp_path, uploads):
    (tmp_path / "old.csv").write_text("old")
    watcher, _ = make(server, tmp_path, only_new=True)
    watcher.prime()

    watcher.scan_once()
    assert watcher.stats.uploaded == 0

    (tmp_path / "new.csv").write_text("new")
    watcher.scan_once()
    assert watcher.stats.uploaded == 1


def test_history_survives_a_restart(server, tmp_path, uploads):
    (tmp_path / "run.csv").write_text("alpha")
    first, _ = make(server, tmp_path)
    first.scan_once()

    second, _ = make(server, tmp_path)
    second.scan_once()

    assert second.stats.uploaded == 0
    assert len(uploads) == 1


def test_dry_run_sends_nothing(server, tmp_path, uploads):
    (tmp_path / "run.csv").write_text("alpha")
    watcher, events = make(server, tmp_path, dry_run=True)
    watcher.scan_once()
    assert uploads == []
    assert any(e.kind == "skipped" for e in events)


def test_min_age_defers_files_still_being_written(server, tmp_path, uploads):
    (tmp_path / "run.csv").write_text("alpha")
    watcher, _ = make(server, tmp_path)
    watcher.settings.min_age = 60
    watcher.scan_once()
    assert uploads == []


def test_stop_ends_the_run_loop(server, tmp_path):
    watcher, _ = make(server, tmp_path)
    watcher.settings.interval = 0.05
    watcher.stop()
    stats = watcher.run()
    assert stats.scans == 0
