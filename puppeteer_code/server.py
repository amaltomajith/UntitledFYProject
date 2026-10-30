"""
puppeteer_code/server.py
========================
Local web server behind the console (`pcode serve`).

  RunManager      one active run at a time, in a worker thread, with a per-run
                  event log that the browser reads over server-sent events.
  create_app      the JSON API plus the static site from web/out.
  export_web      writes web/public/data/ for the hosted site. Real providers only.

This server can start model runs and write to a repository, so it binds to
127.0.0.1 and refuses requests whose Host or Origin is not local. Without that
check any website could reach it through the visitor's browser.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from puppeteer_code.cli import ROOT, apply_diff, make_session
from puppeteer_code.engine import CodeOrchestrator, RunConfig
from puppeteer_code.policy import make_policy
from puppeteer_code.providers import load_config
from puppeteer_code.workspace import scrub, scrub_obj as _clean

logger = logging.getLogger(__name__)

POLICIES = ["heuristic", "linear", "llm", "fixed", "random"]
REAL_PROVIDERS = {"groq", "nebius"}     # the only data allowed on the public site
DEV_PORT = 3000                         # `next dev`
LINEAR_WEIGHTS = ("pcode_linear.json", "pcode_linear_groq.json", "pcode_linear_sim.json")
KEEP_LOGS = 8

_RUN_ID = re.compile(r"^fix-[0-9A-Za-z-]{1,40}$")

BENCH_LABELS = {
    "bench-groq-test.json": ("PCodeBench-Mini", "test", "gpt-oss-120b"),
    "bench-groq-repo.json": ("PCodeBench-Repo", "test", "gpt-oss-120b"),
    "bench-groq-repo-20b.json": ("PCodeBench-Repo", "test", "gpt-oss-20b"),
}
TRAIN_NOTES = {"train-groq.json": "Warm-started from weights trained on the simulator."}


class RunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=4000)
    repo: str = Field(min_length=1, max_length=1000)
    provider: Optional[str] = None
    policy: str = "heuristic"
    max_steps: int = Field(default=12, ge=1, le=40)
    tdd: bool = False


class ApplyRequest(BaseModel):
    confirm: bool = False


class RunBusy(Exception):
    pass


class LiveRun:
    def __init__(self, run_id: str, task: str, repo: Path, provider: str):
        self.id = run_id
        self.task = task
        self.repo = repo
        self.provider = provider
        self.events: list[dict] = []
        self.cancel = threading.Event()
        self.done = False
        self.result: Optional[dict] = None
        self.error: Optional[str] = None


class RunManager:
    def __init__(self, runs_dir: Path, session_factory: Callable):
        self.runs_dir = Path(runs_dir)
        self.session_factory = session_factory
        self.runs: dict[str, LiveRun] = {}
        self.active: Optional[LiveRun] = None
        self._lock = threading.Lock()

    def start(self, req: RunRequest) -> LiveRun:
        repo = Path(req.repo).expanduser()
        if not repo.is_absolute() or not repo.is_dir():
            raise ValueError(f"No such folder: {req.repo}. Give the full path to the repository.")
        repo = repo.resolve()
        if repo.parent == repo:
            raise ValueError("Pick a repository folder, not a whole drive.")
        if req.policy not in POLICIES:
            raise ValueError(f"Unknown policy: {req.policy}")
        if req.provider and req.provider not in load_config()["providers"]:
            raise ValueError(f"Unknown provider: {req.provider}")

        with self._lock:
            if self.active is not None:
                raise RunBusy(self.active.id)
            stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            run = LiveRun(f"fix-{stamp}-{secrets.token_hex(2)}", req.task, repo, req.provider or "")
            self.active = run          # reserved before the slow session set-up below
        try:
            session = self.session_factory(req.provider)
            run.provider = session.provider
            policy = make_policy(req.policy, session=session, weights=_linear_weights(), seed=None)
        except BaseException:
            with self._lock:
                self.active = None
            raise

        with self._lock:
            self.runs[run.id] = run
            for old in [r for r in self.runs.values() if r.done][:-KEEP_LOGS]:
                del self.runs[old.id]
        config = RunConfig(max_steps=req.max_steps, tdd=req.tdd)
        threading.Thread(target=self._work, args=(run, session, policy, config), daemon=True).start()
        return run

    def _emit(self, run: LiveRun, event: dict) -> None:
        if event["type"] == "end":
            event = {**event, "result": event["result"].to_dict()}
        event = {**_clean(event), "seq": len(run.events)}
        run.events.append(event)

    def _work(self, run: LiveRun, session, policy, config: RunConfig) -> None:
        try:
            orch = CodeOrchestrator(session, policy=policy, config=config,
                                    on_event=lambda e: self._emit(run, e), cancel=run.cancel)
            result = orch.run(run.repo, run.task)
            run.result = _clean(result.to_dict())
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            (self.runs_dir / f"{run.id}.json").write_text(json.dumps(run.result, indent=1), encoding="utf-8")
        except Exception as e:      # a bad repo or a provider outage must reach the browser
            logger.exception("Run %s failed", run.id)
            run.error = scrub(f"{type(e).__name__}: {e}")[:500]
            self._emit(run, {"type": "error", "message": run.error})
        finally:
            run.done = True
            with self._lock:
                self.active = None

    def load(self, run_id: str) -> Optional[dict]:
        if not _RUN_ID.match(run_id):
            return None
        live = self.runs.get(run_id)
        if live is not None and live.result is not None:
            return live.result
        path = self.runs_dir / f"{run_id}.json"
        if not path.is_file():
            return None
        return _clean(json.loads(path.read_text(encoding="utf-8")))

    def history(self) -> list[dict]:
        rows = []
        for path in sorted(self.runs_dir.glob("fix-*.json"), reverse=True):
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            ft = (d.get("final") or {}).get("final_tests") or {}
            rows.append({
                "id": path.stem, "task": scrub(str(d.get("task", ""))), "repo": d.get("repo"),
                "provider": d.get("provider") or "unknown", "policy": d.get("policy"),
                "status": d.get("status"), "success": d.get("success"),
                "steps": len(d.get("steps") or []), "tokens": (d.get("usage") or {}).get("total_tokens"),
                "passed": ft.get("passed"), "total": ft.get("total"), "wall_s": d.get("wall_s"),
                "created": _dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            })
        return rows


def _linear_weights() -> str:
    for name in LINEAR_WEIGHTS:
        path = ROOT / "checkpoints" / name
        if path.is_file():
            return str(path)
    return str(ROOT / "checkpoints" / LINEAR_WEIGHTS[0])


def default_session(provider: Optional[str], mock_solution: Optional[str] = None):
    import argparse
    return make_session(argparse.Namespace(provider=provider, mock_solution=mock_solution, cache=None))


# ── request guard ────────────────────────────────────────────────────────────

class LocalOnly:
    """Refuse anything that did not come from this machine's own pages."""

    def __init__(self, app, port: int):
        self.app = app
        self.hosts = {"127.0.0.1", "localhost"}
        self.origins = {f"http://{h}:{p}" for h in self.hosts for p in (port, DEV_PORT)}

    def _allowed(self, headers: dict[bytes, bytes]) -> bool:
        host = headers.get(b"host", b"").decode("latin-1")
        if urlsplit(f"//{host}").hostname not in self.hosts:
            return False
        origin = headers.get(b"origin", b"").decode("latin-1")
        if origin and origin not in self.origins:
            return False
        return headers.get(b"sec-fetch-site", b"") != b"cross-site"

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or self._allowed(dict(scope["headers"])):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await receive()
            await send({"type": "websocket.close", "code": 4403})
            return
        await JSONResponse({"detail": "Requests must come from this machine."}, status_code=403)(
            scope, receive, send)


# ── site data ────────────────────────────────────────────────────────────────

def _dataset(rows: list[dict]) -> str:
    return "PCodeBench-Repo" if any(str(r.get("task", "")).startswith("repo_") for r in rows) else "PCodeBench-Mini"


def collect_benchmarks(runs_dir: Path) -> list[dict]:
    out = []
    for path in sorted(Path(runs_dir).glob("bench-*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("provider") not in REAL_PROVIDERS or "probe" in path.stem:
            continue
        all_rows = d.get("rows") or []
        rows = [r for r in all_rows if not r.get("provider_error")]
        if not rows:
            continue
        dataset, split, model = BENCH_LABELS.get(path.name, (_dataset(rows), None, None))
        methods = d.get("methods") or sorted({r["method"] for r in rows})
        per_task: dict[str, set] = {}
        for r in rows:
            per_task.setdefault(r["task"], set()).add(r["method"])
        keep = ("method", "task", "seed", "success", "tokens", "steps", "max_prompt_chars", "wall_s")
        out.append({
            "id": path.stem, "provider": d["provider"], "dataset": dataset, "split": split, "model": model,
            "methods": methods, "seeds": d.get("seeds"), "partial": bool(d.get("partial")),
            "tasks_planned": len(d.get("tasks") or []), "tasks_measured": len(per_task),
            "tasks_all_methods": sum(1 for m in per_task.values() if len(m) == len(methods)),
            "runs": len(rows), "provider_errors": len(all_rows) - len(rows),
            "summary": d.get("summary") or {},
            "rows": [{k: r.get(k) for k in keep} for r in rows],
        })
    return out


def collect_training(runs_dir: Path) -> list[dict]:
    out = []
    for path in sorted(Path(runs_dir).glob("train-*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("provider") not in REAL_PROVIDERS:
            continue
        keep = ("episode", "task", "success", "tokens", "steps", "return", "terminal_reward")
        out.append({
            "id": path.stem, "provider": d["provider"], "lr": d.get("lr"),
            "note": TRAIN_NOTES.get(path.name),
            "episodes": [{k: e.get(k) for k in keep} for e in d.get("episodes") or []],
        })
    return out


def export_web(runs_dir: Path, out_dir: Path) -> dict:
    """Write the hosted site's data files. Simulated and scripted data never pass this point."""
    runs_dir, out_dir = Path(runs_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"benchmarks": collect_benchmarks(runs_dir), "training": collect_training(runs_dir)}
    for name, data in stats.items():
        (out_dir / f"{name}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")

    demo = None
    for path in sorted(runs_dir.glob("fix-*.json"), reverse=True):
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("provider") in REAL_PROVIDERS and d.get("success"):
            demo = _clean({**d, "id": path.stem, "repo": Path(str(d.get("repo"))).name})
            break
    if demo is not None:
        (out_dir / "demo-run.json").write_text(json.dumps(demo, indent=1), encoding="utf-8")
    return {"benchmarks": len(stats["benchmarks"]), "training": len(stats["training"]),
            "demo_run": demo["id"] if demo else None}


# ── app ──────────────────────────────────────────────────────────────────────

def create_app(runs_dir: Optional[Path] = None, static_dir: Optional[Path] = None,
               session_factory: Optional[Callable] = None, port: int = 8765, ide=None) -> FastAPI:
    runs_dir = Path(runs_dir or ROOT / "runs")
    static_dir = Path(static_dir or ROOT / "web" / "out")
    manager = RunManager(runs_dir, session_factory or default_session)
    app = FastAPI(title="Puppeteer-Code", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager = manager
    app.add_middleware(LocalOnly, port=port)

    @app.middleware("http")
    async def dev_cors(request: Request, call_next):
        # LocalOnly has already vetted the origin; this lets `next dev` read responses.
        if request.method == "OPTIONS":
            response = JSONResponse({})
        else:
            response = await call_next(request)
        origin = request.headers.get("origin")
        if origin and origin.endswith(f":{DEV_PORT}"):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "content-type, last-event-id, x-pcode-token"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT"
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/api/health")
    def health():
        cfg = load_config()
        providers = cfg["providers"]
        keys = {name: bool(os.environ.get(spec["api_key_env"]))
                for name, spec in providers.items() if spec.get("api_key_env")}
        active = manager.active
        return {"ok": True, "app": "puppeteer-code", "keys": keys,
                "tavily": bool(os.environ.get("TAVILY_API_KEY")),
                "default_provider": cfg.get("default_provider"), "active_run": active.id if active else None}

    @app.get("/api/providers")
    def providers():
        cfg = load_config()
        out = []
        for name, spec in cfg["providers"].items():
            env = spec.get("api_key_env")
            tiers = {t: (c[0] if isinstance(c, list) else c) for t, c in (spec.get("tiers") or {}).items()}
            out.append({"id": name, "needs_key": bool(env), "key_set": not env or bool(os.environ.get(env)),
                        "key_name": env, "tiers": tiers, "real": name in REAL_PROVIDERS})
        return {"providers": out, "default": cfg.get("default_provider"), "policies": POLICIES}

    @app.post("/api/runs", status_code=202)
    def start_run(req: RunRequest):
        try:
            run = manager.start(req)
        except RunBusy as e:
            raise HTTPException(409, {"code": "run_active", "run": str(e)})
        except ValueError as e:
            raise HTTPException(400, {"code": "bad_request", "message": scrub(str(e))})
        except EnvironmentError as e:
            raise HTTPException(400, {"code": "missing_key", "message": scrub(str(e))})
        return {"id": run.id}

    @app.get("/api/runs")
    def list_runs():
        return {"runs": manager.history()}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        data = manager.load(run_id)
        if data is None:
            live = manager.runs.get(run_id)
            if live is None:
                raise HTTPException(404, {"code": "not_found"})
            return {"id": run_id, "status": "error" if live.error else "running",
                    "task": live.task, "repo": str(live.repo), "provider": live.provider, "error": live.error}
        return {**data, "id": run_id}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, after: int = -1, last_event_id: Optional[str] = Header(default=None)):
        run = manager.runs.get(run_id)
        if run is None:
            raise HTTPException(404, {"code": "not_found"})
        if last_event_id and last_event_id.isdigit():
            after = int(last_event_id)

        async def stream():
            i = after + 1
            idle = 0.0
            while True:
                batch = run.events[i:]
                for e in batch:
                    yield f"id: {e['seq']}\ndata: {json.dumps(e)}\n\n"
                i += len(batch)
                if run.done and i >= len(run.events):
                    break
                idle = 0.0 if batch else idle + 0.15
                if idle >= 15:
                    idle = 0.0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.15)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    def cancel(run_id: str):
        run = manager.runs.get(run_id)
        if run is None or run.done:
            raise HTTPException(409, {"code": "not_running"})
        run.cancel.set()
        return {"cancelling": True}

    @app.post("/api/runs/{run_id}/apply")
    def apply(run_id: str, req: ApplyRequest):
        data = manager.load(run_id)
        if data is None:
            raise HTTPException(404, {"code": "not_found"})
        if not req.confirm:
            raise HTTPException(400, {"code": "confirm_required"})
        if not data.get("success"):
            raise HTTPException(409, {"code": "not_verified",
                                      "message": "Only a run that passed its tests can be applied."})
        diff = (data.get("final") or {}).get("diff", "")
        if not diff.strip():
            raise HTTPException(409, {"code": "empty_diff", "message": "This run changed nothing."})
        repo = Path(str(data.get("repo")))
        if not repo.is_dir():
            raise HTTPException(409, {"code": "repo_missing", "message": f"Folder not found: {repo}"})
        ok, err = apply_diff(repo, diff)
        if not ok:
            raise HTTPException(409, {"code": "apply_failed", "message": scrub(err)[:500]})
        return {"applied": True, "repo": str(repo)}

    @app.get("/api/benchmarks")
    def benchmarks():
        return {"benchmarks": collect_benchmarks(runs_dir), "training": collect_training(runs_dir)}

    if ide is not None:
        from puppeteer_code.assistant.ide import register_ide
        register_ide(app, ide)

    @app.api_route("/api/{rest:path}", methods=["GET", "POST"], include_in_schema=False)
    def no_route(rest: str):
        raise HTTPException(404, {"code": "not_found"})

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def site(path: str):
        root = static_dir.resolve()
        if not root.is_dir():
            return JSONResponse({"detail": "Site not built. Run `npm run build` in web/."}, status_code=404)
        folder, _, name = path.rpartition("/")
        if name.startswith("__next.!"):      # Next asks for a.b.c.txt, the export stores a.b/c.txt
            first, second, *rest = name.split(".")
            path = f"{folder}/{first}.{second}/{'/'.join(rest[:-1])}.txt".lstrip("/")
        for candidate in (path, f"{path}.html", f"{path}/index.html", "index.html" if not path else path):
            target = (root / candidate).resolve()
            if target.is_file() and target.is_relative_to(root):
                return FileResponse(target)
        return FileResponse(root / "404.html", status_code=404)

    return app


def serve(host_port: int = 8765, mock_solution: Optional[str] = None, ide=None) -> None:
    import uvicorn
    factory = None
    if mock_solution:
        factory = lambda provider: default_session(provider, mock_solution)   # noqa: E731
    uvicorn.run(create_app(session_factory=factory, port=host_port, ide=ide), host="127.0.0.1",
                port=host_port, log_level="warning")
