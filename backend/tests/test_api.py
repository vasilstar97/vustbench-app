"""Tests at the HTTP boundary; calculation behavior is covered by the core."""

from argparse import Namespace

from fastapi.testclient import TestClient
import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

from vustbench_app.api import create_app
from vustbench_app.datasets import DatasetStore
from vustbench_app.runs import RunBusyError, RunManager, RunStore
from vustbench_app.settings import Settings


class FakeDatasets:
    def __init__(self):
        self.layer_calls = []

    def catalog(self):
        return [{"name": "school", "name_ru": "Школы", "available": True}]

    def city_metadata(self):
        return {"name": "Test city", "bounds": [29, 59, 31, 61], "population": 100}

    def layer(self, layer, service, run_id, bbox, zoom):
        self.layer_calls.append((layer, service, run_id, bbox, zoom))
        return {"type": "FeatureCollection", "features": [], "metadata": {"truncated": False}}


@pytest.fixture
def context(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", artifacts_dir=tmp_path / "artifacts")
    datasets = FakeDatasets()
    app = create_app(settings, datasets=datasets)
    with TestClient(app) as client:
        yield client, app, datasets


@pytest.mark.parametrize("bbox", ["a,b,c,d", "1,2,3", "nan,1,2,3", "1,2,inf,3", "3,1,2,4", "-181,1,2,3"])
def test_bad_bbox_rejected_before_loading_layers(context, bbox):
    client, _, datasets = context
    assert client.get("/api/layers/buildings", params={"bbox": bbox}).status_code == 422
    assert datasets.layer_calls == []


def test_viewport_is_parsed_and_forwarded(context):
    client, _, datasets = context
    response = client.get("/api/layers/services", params={"bbox": "29,59,31,61", "zoom": 11})
    assert response.status_code == 200
    assert datasets.layer_calls[-1] == ("services", "school", None, (29.0, 59.0, 31.0, 61.0), 11.0)


@pytest.mark.parametrize("body", [
    {"service": "../../data"}, {"service": "unknown"}, {"mode": "drive"}, {"unexpected": True},
])
def test_create_run_whitelist(context, body):
    client, app, _ = context
    assert client.post("/api/runs", json=body).status_code == 422
    assert app.state.runs.list() == []


def test_failed_or_running_run_cannot_supply_result_layer(context):
    client, app, datasets = context
    run = app.state.runs.create("school", "walk", "pending")
    response = client.get("/api/layers/buildings", params={"run_id": "pending"})
    assert response.status_code == 409
    assert datasets.layer_calls == []
    app.state.runs.mark_failed(run, "No graph available")
    response = client.get("/api/runs/pending")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["summary"] is None


def test_run_id_cannot_escape_artifact_directory(context):
    client, _, _ = context
    assert client.get("/api/layers/services", params={"run_id": "../secrets"}).status_code == 422
    assert client.get("/api/runs/invalid.name").status_code == 422


def test_post_returns_accepted_and_location(context, monkeypatch):
    client, app, _ = context
    monkeypatch.setattr(app.state.manager, "submit", lambda service, mode: app.state.runs.create(service, mode, "queued"))
    response = client.post("/api/runs", json={"service": "school", "mode": "walk"})
    assert response.status_code == 202
    assert response.headers["location"] == "/api/runs/queued"
    assert response.json()["status"] == "queued"
    assert response.json()["summary"] is None


def test_busy_compute_is_explicit(context, monkeypatch):
    client, app, _ = context

    def busy(*_args):
        raise RunBusyError("A calculation is running")

    monkeypatch.setattr(app.state.manager, "submit", busy)
    assert client.post("/api/runs", json={}).status_code == 409


def test_startup_recovers_interrupted_run_without_fake_summary(tmp_path):
    store = RunStore(tmp_path / "artifacts")
    store.create("school", "walk", "interrupted")
    settings = Settings(data_dir=tmp_path, artifacts_dir=tmp_path / "artifacts")
    with TestClient(create_app(settings, datasets=FakeDatasets())) as client:
        run = client.get("/api/runs/interrupted").json()
    assert run["status"] == "failed"
    assert run["summary"] is None


def test_spatial_filter_zoom_and_limit_are_explicit(tmp_path):
    settings = Settings(data_dir=tmp_path, artifacts_dir=tmp_path, max_layer_features=1)
    store = DatasetStore(settings, RunStore(tmp_path))
    frame = gpd.GeoDataFrame(
        {"id": ["a", "b", "outside"], "capacity": [10, 20, 30]},
        geometry=[Point(30, 60), Point(30.1, 60), Point(40, 60)], crs=4326,
    )
    store._frames[("services", "school", None)] = frame
    result = store.layer("services", "school", None, (29, 59, 31, 61), 12)
    assert len(result["features"]) == 1
    assert result["metadata"]["matched_count"] == 2
    assert result["metadata"]["truncated"] is True
    assert store.layer("buildings", "school", None, None, 11)["features"] == []


def test_service_footprints_are_markers_without_mutating_source(tmp_path):
    settings = Settings(data_dir=tmp_path, artifacts_dir=tmp_path)
    store = DatasetStore(settings, RunStore(tmp_path))
    polygon = Polygon([(30, 60), (30.1, 60), (30.1, 60.1), (30, 60.1)])
    frame = gpd.GeoDataFrame({"id": ["school"], "capacity": [100]}, geometry=[polygon], crs=4326)
    store._frames[("services", "school", None)] = frame
    result = store.layer("services", "school", None, None, 12)
    assert result["features"][0]["geometry"]["type"] == "Point"
    assert result["features"][0]["properties"]["capacity"] == 100
    assert frame.geometry.iloc[0].equals(polygon)


def test_worker_utf8_and_graph_deadline(tmp_path, monkeypatch):
    import vustbench_app.runs as run_module

    clock = [0.0]
    captured = {}

    class Process:
        terminated = False

        def __init__(self, _command, **kwargs):
            captured.update(kwargs)

        def poll(self):
            return -1 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return -1

    monkeypatch.setattr(run_module.subprocess, "Popen", Process)
    monkeypatch.setattr(run_module, "monotonic", lambda: clock[0])
    settings = Settings(data_dir=tmp_path, artifacts_dir=tmp_path, graph_timeout_seconds=10)
    store = RunStore(tmp_path)
    manager = RunManager(settings, store)
    run = manager.submit("school", "walk")
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONUNBUFFERED"] == "1"
    store.write({**run, "status": "running", "stage": "graph"})
    manager.reap()
    clock[0] = 11
    manager.reap()
    failed = store.read(run["id"])
    assert failed["status"] == "failed"
    assert failed["error"]["type"] == "TimeoutError"
    assert failed["summary"] is None
    assert manager.processes == {}


def test_cli_failure_message_keeps_raw_details_out_of_ui(tmp_path, monkeypatch):
    import vustbench_core
    from vustbench_app.__main__ import compute

    monkeypatch.setattr(vustbench_core, "load_catalog", lambda _: [{"name": "school", "available": True}])

    def failure(**_kwargs):
        raise ValueError("GEOSException: invalid coordinates 30.001 60.002")

    monkeypatch.setattr(vustbench_core, "run_baseline", failure)
    for name in ["OVERPASS_CACHE_DIR", "OVERPASS_TIMEOUT", "OVERPASS_MAX_RETRIES", "LOG_LEVEL"]:
        monkeypatch.delenv(name, raising=False)
    result = compute(Namespace(
        data_dir=tmp_path, artifacts_dir=tmp_path, service="school", mode="walk",
        run_id="failure", graph_path=None, osm_id=None,
    ))
    run = RunStore(tmp_path).read("failure")
    assert result == 1
    assert run["status"] == "failed"
    assert "GEOSException" not in run["message"]
    assert "30.001" not in run["message"]
    assert run["error"]["type"] == "ValueError"
    assert "30.001" in run["error"]["details"]
