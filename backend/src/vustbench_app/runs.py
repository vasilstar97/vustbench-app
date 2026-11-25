"""Persistent run records and a bounded, local subprocess queue."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import RLock
from time import monotonic
from typing import Any
from uuid import uuid4

from .settings import Settings


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
ACTIVE_STATUSES = {"queued", "running"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Run id must contain 1–80 letters, digits, underscores or hyphens.")
    return run_id


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8]


def normalize_run(raw: dict[str, Any]) -> dict[str, Any]:
    """Support existing core records while keeping the web API stable."""
    run = dict(raw)
    run["id"] = raw.get("id", raw.get("run_id"))
    run["service"] = raw.get("service", raw.get("service_name"))
    run.setdefault("updated_at", raw.get("created_at"))
    run.setdefault("message", "Расчёт завершён" if raw.get("status") == "completed" else "")
    run.setdefault("summary", None)
    run.setdefault("parameters", {})
    return run


class RunStore:
    def __init__(self, artifacts_dir: Path):
        self.runs_dir = artifacts_dir / "runs"

    def directory(self, run_id: str) -> Path:
        return self.runs_dir / validate_run_id(run_id)

    def read(self, run_id: str) -> dict[str, Any]:
        path = self.directory(run_id) / "manifest.json"
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError("Run manifest must be a JSON object.")
        run = normalize_run(raw)
        if run["id"] != run_id:
            raise ValueError("Run manifest id does not match its directory.")
        return run

    def write(self, run: dict[str, Any]) -> None:
        path = self.directory(run["id"])
        path.mkdir(parents=True, exist_ok=True)
        temporary = path / f"manifest.{os.getpid()}.{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(run, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
            )
            temporary.replace(path / "manifest.json")
        finally:
            temporary.unlink(missing_ok=True)

    def list(self, service: str | None = None) -> list[dict[str, Any]]:
        if not self.runs_dir.exists():
            return []
        runs = []
        for path in self.runs_dir.glob("*/manifest.json"):
            try:
                run = self.read(path.parent.name)
            except (OSError, ValueError, TypeError):
                continue
            if service is None or run.get("service") == service:
                runs.append(run)
        return sorted(runs, key=lambda run: run.get("created_at") or "", reverse=True)

    def create(self, service: str, mode: str, run_id: str | None = None, *, owner: str = "api") -> dict[str, Any]:
        run_id = validate_run_id(run_id) if run_id else new_run_id()
        # Refuse accidental reuse, including a completed run from a previous session.
        self.directory(run_id).mkdir(parents=True, exist_ok=False)
        now = utc_now()
        run = {
            "id": run_id,
            "status": "queued",
            "service": service,
            "mode": mode,
            "created_at": now,
            "updated_at": now,
            "message": "Расчёт поставлен в очередь",
            "summary": None,
            "parameters": {},
            "owner": owner,
        }
        self.write(run)
        return run

    def mark_failed(
        self, run: dict[str, Any], message: str, *, error: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        failed = {**run, "status": "failed", "updated_at": utc_now(), "message": message, "summary": None}
        if error is not None:
            failed["error"] = error
        self.write(failed)
        return failed

    def recover_interrupted(self) -> None:
        for run in self.list():
            if run.get("status") in ACTIVE_STATUSES and run.get("owner", "api") == "api":
                self.mark_failed(run, "Расчёт прерван перезапуском приложения. Запустите его повторно.")


class RunBusyError(Exception):
    pass


class RunManager:
    """One compute process per application; use one Uvicorn worker."""

    def __init__(self, settings: Settings, store: RunStore):
        self.settings = settings
        self.store = store
        self.processes: dict[str, subprocess.Popen] = {}
        self._started_at: dict[str, float] = {}
        self._stage_started_at: dict[str, tuple[str | None, float]] = {}
        self._lock = RLock()

    def submit(self, service: str, mode: str) -> dict[str, Any]:
        with self._lock:
            self.reap()
            if self.processes:
                raise RunBusyError("Выполняется другой расчёт. Дождитесь его завершения.")
            run = self.store.create(service, mode)
            command = [
                sys.executable, "-m", "vustbench_app", "compute",
                "--data-dir", str(self.settings.data_dir),
                "--artifacts-dir", str(self.settings.artifacts_dir),
                "--service", service, "--mode", mode, "--run-id", run["id"],
            ]
            if self.settings.graph_path:
                command += ["--graph-path", str(self.settings.graph_path)]
            if self.settings.osm_id:
                command += ["--osm-id", str(self.settings.osm_id)]
            environment = os.environ.copy()
            environment.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1")
            try:
                with (self.store.directory(run["id"]) / "worker.log").open("ab") as output:
                    process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, env=environment)
                self.processes[run["id"]] = process
                self._started_at[run["id"]] = monotonic()
            except OSError:
                self.store.mark_failed(run, "Не удалось запустить процесс расчёта.")
                raise
            return run

    def reap(self) -> None:
        with self._lock:
            for run_id, process in list(self.processes.items()):
                exit_code = process.poll()
                if exit_code is None:
                    run = self.store.read(run_id)
                    now = monotonic()
                    stage = run.get("stage")
                    previous_stage, stage_started = self._stage_started_at.get(run_id, (None, now))
                    if stage != previous_stage:
                        stage_started = now
                    self._stage_started_at[run_id] = (stage, stage_started)
                    run_expired = now - self._started_at[run_id] > self.settings.run_timeout_seconds
                    graph_expired = stage == "graph" and now - stage_started > self.settings.graph_timeout_seconds
                    if run_expired or graph_expired:
                        self._stop_process(process)
                        self.store.mark_failed(
                            run, "Превышено время ожидания расчёта. Повторите запуск или используйте сохранённый граф.",
                            error={"type": "TimeoutError", "details": "Graph deadline exceeded" if graph_expired else "Run deadline exceeded", "stage": stage},
                        )
                        del self.processes[run_id]
                        self._started_at.pop(run_id, None)
                        self._stage_started_at.pop(run_id, None)
                    continue
                try:
                    run = self.store.read(run_id)
                    if run.get("status") in ACTIVE_STATUSES:
                        self.store.mark_failed(
                            run, f"Процесс расчёта завершился без результата (код {exit_code}). См. worker.log."
                        )
                finally:
                    del self.processes[run_id]
                    self._started_at.pop(run_id, None)
                    self._stage_started_at.pop(run_id, None)

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def shutdown(self) -> None:
        with self._lock:
            for run_id, process in list(self.processes.items()):
                self._stop_process(process)
                run = self.store.read(run_id)
                if run.get("status") in ACTIVE_STATUSES:
                    self.store.mark_failed(run, "Расчёт прерван остановкой приложения.")
            self.processes.clear()
            self._started_at.clear()
            self._stage_started_at.clear()
