"""Run the API or compute a persistent baseline from the command line."""

import argparse
import json
import os
from pathlib import Path
import sys
import traceback

from .runs import ACTIVE_STATUSES, RunStore, normalize_run, utc_now, validate_run_id
from .settings import Settings


def compute(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    data_dir = (args.data_dir or settings.data_dir).resolve()
    artifacts_dir = (args.artifacts_dir or settings.artifacts_dir).resolve()
    # IduEdu reads these variables during import. Keep local and Docker runs
    # on the same writable cache, and limit retries of each network request.
    os.environ.setdefault("OVERPASS_CACHE_DIR", str(artifacts_dir / "cache" / "iduedu"))
    os.environ.setdefault("OVERPASS_TIMEOUT", "120")
    os.environ.setdefault("OVERPASS_MAX_RETRIES", "1")
    os.environ.setdefault("LOG_LEVEL", "INFO")
    from vustbench_core import load_catalog, run_baseline

    store = RunStore(artifacts_dir)
    if args.run_id:
        validate_run_id(args.run_id)
        try:
            run = store.read(args.run_id)
        except FileNotFoundError:
            run = store.create(args.service, args.mode, args.run_id, owner="cli")
        else:
            if run["status"] not in ACTIVE_STATUSES:
                raise ValueError("A finished run cannot be overwritten. Choose a new run id.")
            if run["service"] != args.service or run["mode"] != args.mode:
                raise ValueError("Run id belongs to different calculation parameters.")
    else:
        run = store.create(args.service, args.mode, owner="cli")

    def progress(stage: str, message: str) -> None:
        run.update(status="running", stage=stage, message=message, updated_at=utc_now())
        store.write(run)
        print(f"[{stage}] {message}", flush=True)

    try:
        item = next((item for item in load_catalog(data_dir) if item["name"] == args.service), None)
        if item is None:
            raise ValueError(f"Unknown service type: {args.service}")
        if item.get("available") is False:
            raise ValueError(item.get("error") or f"Service {args.service} has no input layer.")
        progress("starting", "Подготовка расчёта")
        result = run_baseline(
            data_dir=data_dir,
            artifacts_dir=artifacts_dir,
            service_name=args.service,
            mode=args.mode,
            run_id=run["id"],
            progress=progress,
            graph_path=args.graph_path or settings.graph_path,
            osm_id=args.osm_id or settings.osm_id,
            export_geojson=not getattr(args, "no_geojson", False),
        )
        result = normalize_run(result)
        if result.get("status") != "completed" or result.get("id") != run["id"]:
            raise ValueError("Core returned an incomplete or mismatched run manifest.")
        result.update(created_at=run["created_at"], updated_at=utc_now(), message="Расчёт завершён")
        store.write(result)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
        return 0
    except Exception as error:
        traceback.print_exc()
        store.mark_failed(
            run,
            "Не удалось выполнить расчёт. Подробности сохранены в журнале расчёта.",
            error={"type": type(error).__name__, "details": str(error), "stage": run.get("stage")},
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VUSTBench application and baseline runner")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the HTTP API (one worker)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--prepare", action="store_true", help="Prepare file-defined baseline before starting the viewer")
    commands.add_parser("prepare", help="Prepare all indicators declared in data/indicators.json")
    baseline = commands.add_parser("compute", help="Compute and persist a service-provision baseline")
    baseline.add_argument("--data-dir", type=Path)
    baseline.add_argument("--artifacts-dir", type=Path)
    baseline.add_argument("--service", default="school")
    baseline.add_argument("--mode", choices=["walk"], default="walk")
    baseline.add_argument("--run-id")
    baseline.add_argument("--graph-path", type=Path, help="Prepared IduEdu .urbangraph file")
    baseline.add_argument("--osm-id", type=int, help="OSM territory relation id, if required by the core")
    baseline.add_argument("--no-geojson", action="store_true", help="Save Parquet results without duplicate GeoJSON exports")
    args = parser.parse_args(argv)
    if args.command == "compute":
        return compute(args)
    if args.command == "prepare" or args.prepare:
        from .prepare import prepare
        code = prepare(Settings.from_env())
        if args.command == "prepare":
            return code
    import uvicorn

    uvicorn.run("vustbench_app.api:create_app", factory=True, host=args.host, port=args.port, workers=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
