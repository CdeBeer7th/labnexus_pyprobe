"""End-to-end cover for spectrometer queues: parse at the bench, send structured."""

from __future__ import annotations

import json

import pytest
from conftest import WORKSPACE_ID
from labnexus_plate_parsers import SpectrometerModel, UnifiedPlateReaderOutput

from labnexus_pyprobe.client import LabNexusClient, UploadError
from labnexus_pyprobe.config import Queue, Settings, normalise_server
from labnexus_pyprobe.watcher import Watcher


def make(server, tmp_path, queues, **kwargs):
    settings = Settings(
        server=normalise_server(server, "http", allow_http=True),
        queues=queues,
        workspace_id=kwargs.pop("workspace_id", WORKSPACE_ID),
        min_age=0,
        state_file=tmp_path / "state.json",
        **kwargs,
    )
    client = LabNexusClient(settings.server)
    client.login("me@lab.org", "hunter2")
    events = []
    return Watcher(settings, client, on_event=events.append), events


class TestStructuredUpload:
    def test_sends_the_parsed_document_alongside_the_file(
        self, server, tmp_path, spark_export, spectrometer_uploads
    ):
        drop = tmp_path / "spark"
        drop.mkdir()
        (drop / "run.xlsx").write_bytes(spark_export.read_bytes())

        watcher, events = make(
            server, tmp_path, [Queue(directory=drop, model=SpectrometerModel.tecanSpark)]
        )
        watcher.scan_once()

        assert watcher.stats.uploaded == 1
        assert watcher.stats.parsed == 1
        assert len(spectrometer_uploads) == 1

        sent = spectrometer_uploads[0]
        # The structured payload has to be a valid document, not just any JSON.
        document = UnifiedPlateReaderOutput.model_validate_json(sent["structured"])
        assert document.measurement_groups
        assert document.measurement_count == 45
        assert document.metadata.file.file_name == "run.xlsx"
        # The raw file goes up too, so the server keeps the original.
        assert spark_export.read_bytes()[:4] in sent["raw"]

    def test_targets_the_selected_workspace(
        self, server, tmp_path, spark_export, spectrometer_uploads
    ):
        (tmp_path / "run.xlsx").write_bytes(spark_export.read_bytes())
        watcher, _ = make(
            server, tmp_path, [Queue(directory=tmp_path, model=SpectrometerModel.tecanSpark)]
        )
        watcher.scan_once()

        query = spectrometer_uploads[0]["query"]
        assert f"workspace_id={WORKSPACE_ID}" in query
        assert "prober=true" in query

    def test_emits_a_parsed_event_describing_the_run(
        self, server, tmp_path, spark_export
    ):
        (tmp_path / "run.xlsx").write_bytes(spark_export.read_bytes())
        watcher, events = make(
            server, tmp_path, [Queue(directory=tmp_path, model=SpectrometerModel.tecanSpark)]
        )
        watcher.scan_once()

        parsed = [e for e in events if e.kind == "parsed"]
        assert len(parsed) == 1
        assert "45 series" in parsed[0].detail
        assert parsed[0].instrument == "Tecan Spark"

    def test_plain_queues_still_upload_unparsed(self, server, tmp_path, uploads):
        (tmp_path / "notes.csv").write_text("alpha")
        watcher, _ = make(server, tmp_path, [Queue(directory=tmp_path)])
        watcher.scan_once()

        assert watcher.stats.uploaded == 1
        assert watcher.stats.parsed == 0
        assert b"alpha" in uploads[0]


class TestParseFailures:
    def test_an_unparseable_file_is_never_uploaded(
        self, server, tmp_path, spectrometer_uploads
    ):
        """No point spending the upload on something the server would reject."""
        (tmp_path / "run.xlsx").write_bytes(b"this is not a workbook")
        watcher, events = make(
            server, tmp_path, [Queue(directory=tmp_path, model=SpectrometerModel.tecanSpark)]
        )
        watcher.scan_once()

        assert spectrometer_uploads == []
        assert watcher.stats.uploaded == 0
        assert watcher.stats.failed == 1
        failure = next(e for e in events if e.kind == "failed")
        assert "Could not parse" in failure.message
        assert "Tecan Spark" in failure.detail

    def test_a_failed_parse_is_retried_on_the_next_scan(self, server, tmp_path, spark_export):
        """A half-written export must not be written off permanently."""
        target = tmp_path / "run.xlsx"
        target.write_bytes(b"still being flushed")
        watcher, _ = make(
            server, tmp_path, [Queue(directory=tmp_path, model=SpectrometerModel.tecanSpark)]
        )
        watcher.scan_once()
        assert watcher.stats.uploaded == 0

        target.write_bytes(spark_export.read_bytes())
        watcher.scan_once()
        assert watcher.stats.uploaded == 1

    def test_a_wrong_extension_is_reported_not_uploaded(
        self, server, tmp_path, spectrometer_uploads
    ):
        (tmp_path / "run.txt").write_text("nope")
        watcher, events = make(
            server,
            tmp_path,
            # An explicit pattern lets through a file the instrument never exports.
            [Queue(directory=tmp_path, model=SpectrometerModel.tecanSpark, patterns=["*.txt"])],
        )
        watcher.scan_once()

        assert spectrometer_uploads == []
        failure = next(e for e in events if e.kind == "failed")
        assert "expected xlsx/xls" in failure.detail


class TestDryRun:
    def test_parses_but_sends_nothing(
        self, server, tmp_path, spark_export, spectrometer_uploads
    ):
        """Setting a queue up is exactly when you want to see the parse result."""
        (tmp_path / "run.xlsx").write_bytes(spark_export.read_bytes())
        watcher, events = make(
            server,
            tmp_path,
            [Queue(directory=tmp_path, model=SpectrometerModel.tecanSpark)],
            dry_run=True,
        )
        watcher.scan_once()

        assert spectrometer_uploads == []
        assert watcher.stats.parsed == 1
        assert watcher.stats.skipped == 1
        assert any(e.kind == "parsed" for e in events)


class TestMixedQueues:
    def test_each_folder_is_parsed_as_its_own_instrument(
        self, server, tmp_path, spark_export, spectrometer_uploads, uploads
    ):
        spark = tmp_path / "spark"
        misc = tmp_path / "misc"
        spark.mkdir()
        misc.mkdir()
        (spark / "run.xlsx").write_bytes(spark_export.read_bytes())
        (misc / "notes.csv").write_text("alpha")

        watcher, _ = make(
            server,
            tmp_path,
            [
                Queue(directory=spark, model=SpectrometerModel.tecanSpark),
                Queue(directory=misc),
            ],
        )
        watcher.scan_once()

        assert watcher.stats.uploaded == 2
        assert watcher.stats.parsed == 1
        assert len(spectrometer_uploads) == 1
        assert len(uploads) == 1

    def test_a_spark_file_in_the_wrong_queue_fails_rather_than_mislabels(
        self, server, tmp_path, spark_export, spectrometer_uploads
    ):
        (tmp_path / "run.xlsx").write_bytes(spark_export.read_bytes())
        watcher, _ = make(
            server,
            tmp_path,
            [Queue(directory=tmp_path, model=SpectrometerModel.spectroStarNano)],
        )
        watcher.scan_once()

        assert spectrometer_uploads == []
        assert watcher.stats.failed == 1


class TestClient:
    def test_lists_workspaces(self, server):
        client = LabNexusClient(normalise_server(server, "http", allow_http=True))
        client.login("me@lab.org", "hunter2")
        spaces = client.workspaces()

        assert [w.name for w in spaces] == ["Kinetics", "Shared"]
        assert spaces[0].id == WORKSPACE_ID
        assert spaces[0].owned and not spaces[1].owned

    def test_reads_the_servers_model_list(self, server):
        client = LabNexusClient(normalise_server(server, "http", allow_http=True))
        client.login("me@lab.org", "hunter2")
        assert client.server_models()["parser_version"] == "0.1.0"

    def test_refuses_to_upload_without_a_workspace(self, server, tmp_path, spark_export):
        client = LabNexusClient(normalise_server(server, "http", allow_http=True))
        client.login("me@lab.org", "hunter2")
        target = tmp_path / "run.xlsx"
        target.write_bytes(spark_export.read_bytes())

        with pytest.raises(UploadError, match="no workspace"):
            client.upload_spectrometer(target, SpectrometerModel.tecanSpark, "")

    def test_returns_the_servers_response(self, server, tmp_path, spark_export):
        from labnexus_plate_parsers import parse as parse_plate_reader

        client = LabNexusClient(normalise_server(server, "http", allow_http=True))
        client.login("me@lab.org", "hunter2")
        target = tmp_path / "run.xlsx"
        target.write_bytes(spark_export.read_bytes())

        document = parse_plate_reader(SpectrometerModel.tecanSpark, target.read_bytes(), "run.xlsx")
        result = client.upload_spectrometer(
            target, SpectrometerModel.tecanSpark, WORKSPACE_ID, structured=document
        )
        assert result["file_id"]
        assert result["data"]["schema_version"] == "2.0.0"

    def test_api_calls_are_prefixed(self, server):
        client = LabNexusClient(normalise_server(server, "http", allow_http=True))
        assert client._api("/workspaces/").endswith("/api/workspaces/")


class TestRoundTrip:
    def test_what_the_client_sends_survives_the_servers_validation(
        self, server, tmp_path, spark_export, spectrometer_uploads
    ):
        """The server re-validates the payload, so it has to round-trip exactly."""
        (tmp_path / "run.xlsx").write_bytes(spark_export.read_bytes())
        watcher, _ = make(
            server, tmp_path, [Queue(directory=tmp_path, model=SpectrometerModel.tecanSpark)]
        )
        watcher.scan_once()

        sent = spectrometer_uploads[0]["structured"]
        reparsed = UnifiedPlateReaderOutput.model_validate_json(sent)
        assert json.loads(reparsed.model_dump_json()) == json.loads(sent)
