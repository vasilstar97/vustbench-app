import json

import pytest
from fastapi.testclient import TestClient

from vustbench_app.api import create_app
from vustbench_app.indicators import IndicatorStore
from vustbench_app.runs import RunStore
from vustbench_app.settings import Settings


class Datasets:
    def __init__(self):
        self.calls = []

    def catalog(self):
        return [
            {"name": "school", "name_ru": "школа", "available": True, "service_count": 1},
            {"name": "kindergarten", "name_ru": "детский сад", "available": True, "service_count": 1},
            {"name": "absent", "name_ru": "нет слоя", "available": False, "service_count": 0},
        ]

    def city_metadata(self):
        return {"name": "Test city", "population": 3000}

    def layer(self, layer, service, run_id, bbox, zoom, value_field=None):
        self.calls.append((layer, service, run_id, value_field))
        return {"type": "FeatureCollection", "features": [], "metadata": {}}


@pytest.fixture
def context(tmp_path):
    config = {"version": 1, "indicators": [{
        "id": "provision", "number": "9.1.2", "name": "Обеспеченность: {name_ru}",
        "groups": [{"number": "9", "name": "Социальная инфраструктура"},
                   {"number": "9.1", "name": "Образование"}],
        "for_each": "available_service_types", "calculator": "service_provision",
        "result_field": "provision_ratio", "unit": "%", "scale": 100,
        "map": {"layer": "buildings", "overview_layer": "blocks", "overlays": ["services"],
                "field": "provision_value", "range": [0, 1], "colors": ["red", "green"]},
    }]}
    (tmp_path / "indicators.json").write_text(json.dumps(config), encoding="utf8")
    for name in ("buildings.geojson", "blocks.geojson", "bn_service_types.json"):
        (tmp_path / name).write_text("{}")
    (tmp_path / "services").mkdir()
    for name in ("school", "kindergarten"):
        (tmp_path / "services" / f"{name}.geojson").write_text("{}")
    settings = Settings(data_dir=tmp_path, artifacts_dir=tmp_path / "artifacts")
    datasets, runs = Datasets(), RunStore(settings.artifacts_dir)
    store = IndicatorStore(settings, datasets, runs)
    yield settings, datasets, runs, store, config


def completed(runs, store, value=0):
    run = runs.create("school", "walk", "ready")
    run.update(status="completed", summary={"provision_ratio": value}, data_version=store._data_version("school"))
    runs.write(run)
    for layer in ("buildings", "blocks", "services"):
        (runs.directory("ready") / f"{layer}.parquet").touch()
    return run


def test_file_expansion_and_completed_zero_value_ignore_newer_failure(context):
    settings, datasets, runs, store, _ = context
    completed(runs, store)
    runs.mark_failed(runs.create("school", "walk", "newer"), "failure")
    with TestClient(create_app(settings, datasets=datasets)) as client:
        rows = client.get("/api/indicators").json()["items"]
        assert [r["id"] for r in rows] == ["provision:school", "provision:kindergarten"]
        assert rows[0]["value"] == 0
        assert rows[0]["number"] == "9.1.2"
        assert [g["number"] for g in rows[0]["groups"]] == ["9", "9.1"]
        assert rows[0]["map"]["available"]
        assert rows[1]["value"] is None
        assert not rows[1]["map"]["available"]
        assert client.get("/api/indicators/provision:school/layers/blocks").status_code == 200
        assert datasets.calls[-1] == ("blocks", "school", "ready", "provision_value")
        assert client.get("/api/indicators/provision:kindergarten/layers/blocks").status_code == 409
        assert client.get("/api/indicators/unknown/layers/blocks").status_code == 404


def test_changed_inputs_do_not_show_stale_baseline(context):
    settings, _, runs, store, _ = context
    completed(runs, store, 0.7)
    assert store.rows()[0]["value"] == 70
    (settings.data_dir / "services" / "school.geojson").write_text('{"changed":true}')
    assert store.rows()[0]["value"] is None


def test_missing_service_layer_preserves_indicator_row(context):
    settings, _, _, store, _ = context
    (settings.data_dir / "services" / "school.geojson").unlink()
    row = store.rows()[0]
    assert row["number"] == "9.1.2"
    assert row["value"] is None
    assert not row["map"]["available"]


def test_non_provision_indicator_added_only_in_file(context):
    settings, datasets, _, _, config = context
    config["indicators"].append({
        "id": "population", "name": "Население", "calculator": "city_statistic",
        "result_field": "population", "unit": "чел.", "precision": 0,
        "map": {"layer": "buildings", "field": "population", "range": [0, 1000], "colors": ["blue", "red"]},
    })
    (settings.data_dir / "indicators.json").write_text(json.dumps(config), encoding="utf8")
    with TestClient(create_app(settings, datasets=datasets)) as client:
        row = client.get("/api/indicators").json()["items"][-1]
        assert row["id"] == "population"
        assert row["value"] == 3000
        assert client.get("/api/indicators/population/layers/buildings").status_code == 200
        assert datasets.calls[-1] == ("buildings", "", None, "population")
        assert client.get("/api/indicators/population/layers/services").status_code == 404
