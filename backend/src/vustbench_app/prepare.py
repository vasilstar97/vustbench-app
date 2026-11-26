"""Prepare the file-defined baseline once, before serving the viewer."""

from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
import gc

from .datasets import DatasetStore
from .criteria import CriteriaStore
from .indicators import IndicatorStore
from .runs import RunStore
from .settings import Settings


def prepare(settings: Settings) -> int:
    from .__main__ import compute

    runs = RunStore(settings.artifacts_dir)
    datasets = DatasetStore(settings, runs)
    indicators = IndicatorStore(settings, datasets, runs)
    criteria = CriteriaStore(settings, datasets, indicators, runs)
    jobs = {}
    available = {item["name"] for item in datasets.catalog() if item.get("available") is not False}
    for definition in [*indicators.definitions(), *criteria.service_definitions()]:
        if definition["calculator"] == "service_provision":
            source = settings.data_dir / "services" / f"{definition['service']}.geojson"
            if source.is_file() and definition["service"] in available:
                jobs.setdefault(definition["service"], definition)
    failures = 0
    for index, definition in enumerate(jobs.values(), 1):
        prefix = f"[{index}/{len(jobs)}] {definition['name']}"
        if indicators.latest(definition):
            print(f"{prefix}: cached", flush=True)
            continue
        print(f"{prefix}: computing", flush=True)
        run = runs.create(definition["service"], "walk", owner="prepare")
        with (runs.directory(run["id"]) / "worker.log").open("w", encoding="utf8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                code = compute(Namespace(
                    data_dir=settings.data_dir, artifacts_dir=settings.artifacts_dir,
                    service=definition["service"], mode="walk", run_id=run["id"],
                    graph_path=settings.graph_path, osm_id=settings.osm_id, no_geojson=True,
                ))
        saved = runs.read(run["id"])
        if code:
            failures += 1
            print(f"{prefix}: failed ({saved.get('error', {}).get('details', '')})", flush=True)
        else:
            print(f"{prefix}: {saved['summary']['provision_ratio']:.4%}", flush=True)
        gc.collect()
    if criteria.service_definitions():
        payload = criteria.rows()
        print(f"Social support: {len(payload['items'])} dependency nodes prepared", flush=True)
    return 1 if failures else 0
