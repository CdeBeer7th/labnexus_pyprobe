from pathlib import Path

import pytest
from labnexus_plate_parsers import SpectrometerModel

from labnexus_pyprobe.config import HttpDisabledError, Queue, Settings, normalise_server


@pytest.mark.parametrize(
    ("raw", "scheme", "expected"),
    [
        ("lab.example.com", "https", "https://lab.example.com"),
        ("https://lab.example.com/", "http", "https://lab.example.com"),
        ("  lab.example.com  ", "https", "https://lab.example.com"),
    ],
)
def test_normalise_server(raw, scheme, expected):
    assert normalise_server(raw, scheme) == expected


def test_normalise_server_refuses_plain_http_by_default():
    with pytest.raises(HttpDisabledError):
        normalise_server("lab.example.com:8000", "http")
    with pytest.raises(HttpDisabledError):
        normalise_server("http://lab.example.com")


def test_normalise_server_allows_http_with_override():
    assert (
        normalise_server("lab.example.com:8000", "http", allow_http=True)
        == "http://lab.example.com:8000"
    )
    assert normalise_server("http://x", allow_http=True) == "http://x"


def test_patterns_and_excludes(tmp_path: Path):
    queue = Queue(directory=tmp_path, patterns=["*.csv"])
    assert queue.matches(tmp_path / "run.csv")
    assert not queue.matches(tmp_path / "run.txt")
    assert not queue.matches(tmp_path / ".hidden.csv")
    assert not queue.matches(tmp_path / "run.csv.part")


def test_iter_candidates_respects_recursion(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.csv").write_text("a")
    (tmp_path / "sub" / "deep.csv").write_text("b")

    flat = Queue(directory=tmp_path)
    assert [p.name for p in flat.iter_candidates()] == ["top.csv"]

    deep = Queue(directory=tmp_path, recursive=True)
    assert sorted(p.name for p in deep.iter_candidates()) == ["deep.csv", "top.csv"]


def test_state_file_inside_watched_dir_is_never_uploaded(tmp_path: Path):
    state = tmp_path / "uploads.json"
    state.write_text("{}")
    settings = Settings.single(tmp_path, "http://x", state_file=state)
    assert settings.iter_candidates() == []


class TestQueuePatterns:
    def test_a_spectrometer_queue_defaults_to_its_own_extensions(self, tmp_path: Path):
        queue = Queue(directory=tmp_path, model=SpectrometerModel.tecanSpark)
        assert queue.effective_patterns == ["*.xlsx", "*.xls"]
        assert queue.matches(tmp_path / "run.xlsx")
        # An operator's notes in the drop folder are not a Spark export.
        assert not queue.matches(tmp_path / "notes.txt")

    def test_a_txt_instrument_defaults_to_txt(self, tmp_path: Path):
        queue = Queue(directory=tmp_path, model=SpectrometerModel.spectraMax190)
        assert queue.effective_patterns == ["*.txt"]
        assert not queue.matches(tmp_path / "run.xlsx")

    def test_explicit_patterns_win(self, tmp_path: Path):
        queue = Queue(
            directory=tmp_path, model=SpectrometerModel.tecanSpark, patterns=["*.dat"]
        )
        assert queue.effective_patterns == ["*.dat"]

    def test_a_plain_queue_takes_everything(self, tmp_path: Path):
        assert Queue(directory=tmp_path).effective_patterns == ["*"]

    def test_label_names_the_instrument(self, tmp_path: Path):
        queue = Queue(directory=tmp_path, model=SpectrometerModel.biotekEpoch2)
        assert "Agilent Biotek Epoch 2" in queue.label
        assert "any file" in Queue(directory=tmp_path).label


class TestMultipleQueues:
    def test_each_queue_only_claims_its_own_files(self, tmp_path: Path):
        spark = tmp_path / "spark"
        smax = tmp_path / "spectramax"
        spark.mkdir()
        smax.mkdir()
        (spark / "run.xlsx").write_bytes(b"x")
        (smax / "run.txt").write_bytes(b"y")

        settings = Settings(
            server="http://x",
            queues=[
                Queue(directory=spark, model=SpectrometerModel.tecanSpark),
                Queue(directory=smax, model=SpectrometerModel.spectraMax190),
            ],
        )
        found = {path.name: queue.model for queue, path in settings.iter_candidates()}
        assert found == {
            "run.xlsx": SpectrometerModel.tecanSpark,
            "run.txt": SpectrometerModel.spectraMax190,
        }

    def test_spectrometer_queues_are_separable(self, tmp_path: Path):
        settings = Settings(
            server="http://x",
            queues=[
                Queue(directory=tmp_path, model=SpectrometerModel.tecanSpark),
                Queue(directory=tmp_path, model=None),
            ],
        )
        assert len(settings.spectrometer_queues) == 1

    def test_a_file_reachable_from_two_queues_is_claimed_once(self, tmp_path: Path):
        """Otherwise it uploads twice, the second time parsed as the wrong vendor."""
        (tmp_path / "run.xlsx").write_bytes(b"x")
        settings = Settings(
            server="http://x",
            queues=[
                Queue(directory=tmp_path, model=SpectrometerModel.tecanSpark),
                Queue(directory=tmp_path, model=SpectrometerModel.biotekEpoch2),
            ],
        )
        found = settings.iter_candidates()
        assert len(found) == 1
        assert found[0][0].model is SpectrometerModel.tecanSpark

    def test_nested_queues_do_not_double_up(self, tmp_path: Path):
        inner = tmp_path / "inner"
        inner.mkdir()
        (inner / "run.xlsx").write_bytes(b"x")
        settings = Settings(
            server="http://x",
            queues=[
                Queue(directory=tmp_path, recursive=True),
                Queue(directory=inner),
            ],
        )
        assert len(settings.iter_candidates()) == 1
