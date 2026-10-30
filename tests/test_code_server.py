"""Web console server: run lifecycle, request guard, apply rules, public data export."""

import json
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from code_fixtures import CALC_FIX_BLOCK, make_calc_repo

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from puppeteer_code.mock import scripted_responder
from puppeteer_code.providers import ProviderSession
from puppeteer_code.server import collect_benchmarks, create_app, export_web, scrub

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def mock_session(_provider=None):
    return ProviderSession("mock", responder=scripted_responder(solution=CALC_FIX_BLOCK))


def blocked_session(release: threading.Event):
    """A session whose model calls wait, so a run stays active during the test."""
    inner = scripted_responder(solution=CALC_FIX_BLOCK)

    def responder(model, messages):
        release.wait(30)
        return inner(model, messages)
    return ProviderSession("mock", responder=responder)


@pytest.fixture
def client(tmp_path):
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "site",
                     session_factory=mock_session, port=PORT)
    return TestClient(app, base_url=BASE)


def start(client, repo, **extra):
    return client.post("/api/runs", json={"task": "mean() is wrong", "repo": str(repo), "provider": "mock", **extra})


def wait(client, run_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        if data["status"] not in ("running",):
            return data
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def stream(client, run_id, **headers):
    with client.stream("GET", f"/api/runs/{run_id}/events", headers=headers) as r:
        body = "".join(r.iter_text())
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def test_run_streams_events_and_is_saved(client, tmp_path):
    repo = make_calc_repo(tmp_path)
    res = start(client, repo)
    assert res.status_code == 202
    run_id = res.json()["id"]
    events = stream(client, run_id)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "end" and "step" in kinds
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert events[-1]["result"]["provider"] == "mock"

    data = wait(client, run_id)
    assert data["success"] and data["final"]["diff"]
    assert (tmp_path / "runs" / f"{run_id}.json").is_file()
    row = client.get("/api/runs").json()["runs"][0]
    assert row["id"] == run_id and row["provider"] == "mock" and row["success"]


def test_events_catch_up_after_reconnect(client, tmp_path):
    run_id = start(client, make_calc_repo(tmp_path)).json()["id"]
    events = stream(client, run_id)
    later = stream(client, run_id, **{"Last-Event-ID": str(events[1]["seq"])})
    assert [e["seq"] for e in later] == [e["seq"] for e in events[2:]]


def test_second_run_is_refused_while_one_is_active(tmp_path):
    release = threading.Event()
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "site",
                     session_factory=lambda p: blocked_session(release), port=PORT)
    client = TestClient(app, base_url=BASE)
    repo = make_calc_repo(tmp_path)
    run_id = start(client, repo).json()["id"]
    try:
        res = start(client, repo)
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "run_active"
        assert client.get("/api/health").json()["active_run"] == run_id
    finally:
        release.set()
    wait(client, run_id)
    assert client.get("/api/health").json()["active_run"] is None


def test_cancel_stops_after_the_current_step(tmp_path):
    release = threading.Event()
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "site",
                     session_factory=lambda p: blocked_session(release), port=PORT)
    client = TestClient(app, base_url=BASE)
    run_id = start(client, make_calc_repo(tmp_path)).json()["id"]
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 202
    release.set()
    data = wait(client, run_id)
    assert data["status"] == "cancelled"
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 409


def test_bad_input_is_a_form_error(client, tmp_path):
    res = start(client, tmp_path / "missing")
    assert res.status_code == 400 and res.json()["detail"]["code"] == "bad_request"
    res = client.post("/api/runs", json={"task": "x", "repo": str(tmp_path), "provider": "nope"})
    assert res.status_code == 400 and "nope" in res.json()["detail"]["message"]


def test_missing_key_is_reported_without_a_run(tmp_path, monkeypatch):
    def no_key(_provider):
        raise EnvironmentError("GROQ_API_KEY is not set")
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "site", session_factory=no_key, port=PORT)
    client = TestClient(app, base_url=BASE)
    res = start(client, make_calc_repo(tmp_path))
    assert res.status_code == 400 and res.json()["detail"]["code"] == "missing_key"
    assert client.get("/api/health").json()["active_run"] is None


def test_repo_without_tests_ends_as_a_failed_run(client, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    run_id = start(client, empty).json()["id"]
    events = stream(client, run_id)
    assert events[-1]["type"] == "end" and not events[-1]["result"]["success"]


def test_crash_surfaces_as_an_error_event_without_secrets(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_value_that_must_not_leak")

    def boom(self, repo, task):
        raise RuntimeError("provider said gsk_test_value_that_must_not_leak is bad")
    monkeypatch.setattr("puppeteer_code.server.CodeOrchestrator.run", boom)
    run_id = start(client, make_calc_repo(tmp_path)).json()["id"]
    events = stream(client, run_id)
    assert events[-1]["type"] == "error" and "[hidden]" in events[-1]["message"]
    assert "gsk_test" not in json.dumps(events)
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "error"
    assert client.get("/api/health").json()["active_run"] is None


def test_apply_needs_success_and_confirm(client, tmp_path):
    repo = make_calc_repo(tmp_path)
    original = (repo / "calc.py").read_text(encoding="utf-8")
    run_id = start(client, repo).json()["id"]
    wait(client, run_id)
    assert (repo / "calc.py").read_text(encoding="utf-8") == original     # the run itself never writes

    assert client.post(f"/api/runs/{run_id}/apply", json={}).status_code == 400
    assert (repo / "calc.py").read_text(encoding="utf-8") == original

    res = client.post(f"/api/runs/{run_id}/apply", json={"confirm": True})
    assert res.status_code == 200 and res.json()["applied"]
    assert (repo / "calc.py").read_text(encoding="utf-8") != original

    again = client.post(f"/api/runs/{run_id}/apply", json={"confirm": True})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "apply_failed"


def test_apply_refuses_an_unverified_run(client, tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "fix-1.json").write_text(json.dumps({"success": False, "repo": str(tmp_path),
                                                 "final": {"diff": "x"}}), encoding="utf-8")
    res = client.post("/api/runs/fix-1/apply", json={"confirm": True})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "not_verified"


def test_run_ids_cannot_escape_the_runs_folder(client, tmp_path):
    (tmp_path / "secret.json").write_text("{}", encoding="utf-8")
    assert client.get("/api/runs/..%2Fsecret").status_code == 404
    assert client.get("/api/runs/fix-..").status_code == 404


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "http://127.0.0.1:9999"},
    {"Host": "evil.example"},                       # DNS rebinding
    {"Sec-Fetch-Site": "cross-site"},
])
def test_foreign_requests_are_refused(client, headers):
    assert client.get("/api/health", headers=headers).status_code == 403
    assert client.post("/api/runs", json={}, headers=headers).status_code == 403


def test_own_origin_and_dev_origin_are_allowed(client):
    assert client.get("/api/health", headers={"Origin": BASE}).status_code == 200
    res = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_health_reports_booleans_not_key_values(client, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_value_that_must_not_leak")
    res = client.get("/api/health")
    assert "gsk_test_value" not in res.text
    assert res.json()["keys"]["groq"] is True
    assert "gsk_test_value" not in client.get("/api/providers").text


def test_scrub_hides_secret_values(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_value_that_must_not_leak")
    assert scrub("bad key gsk_test_value_that_must_not_leak here") == "bad key [hidden] here"


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_export_refuses_simulated_data(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    row = {"method": "heuristic", "task": "repo_a", "seed": 0, "success": True, "tokens": 10}
    write_json(runs / "bench-sim-repo.json", {"provider": "sim", "rows": [row]})
    write_json(runs / "bench-oracle.json", {"provider": "oracle", "rows": [row]})
    write_json(runs / "bench-groq-repo.json", {"provider": "groq", "methods": ["heuristic"], "seeds": [0],
                                               "tasks": ["repo_a", "repo_b"], "rows": [row], "partial": True})
    write_json(runs / "train-sim-200.json", {"provider": "sim", "episodes": [{"episode": 0}]})
    write_json(runs / "fix-1-mock.json", {"provider": "mock", "success": True})
    write_json(runs / "fix-0-old.json", {"success": True})                     # older file, provider unknown

    info = export_web(runs, tmp_path / "data")
    assert info == {"benchmarks": 1, "training": 0, "demo_run": None}
    data = json.loads((tmp_path / "data" / "benchmarks.json").read_text(encoding="utf-8"))
    assert [b["id"] for b in data] == ["bench-groq-repo"]
    assert data[0]["tasks_measured"] == 1 and data[0]["tasks_planned"] == 2 and data[0]["partial"]
    assert not (tmp_path / "data" / "demo-run.json").exists()


def test_export_keeps_a_real_demo_run_without_the_local_path(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    write_json(runs / "fix-2.json", {"provider": "groq", "success": True, "repo": "C:/Users/someone/project"})
    info = export_web(runs, tmp_path / "data")
    assert info["demo_run"] == "fix-2"
    demo = json.loads((tmp_path / "data" / "demo-run.json").read_text(encoding="utf-8"))
    assert demo["repo"] == "project"


def test_provider_errors_are_counted_not_aggregated(tmp_path):
    good = {"method": "heuristic", "task": "repo_a", "success": True, "tokens": 10}
    bad = {"method": "heuristic", "task": "repo_b", "success": False, "provider_error": "RateLimitError"}
    write_json(tmp_path / "bench-groq-x.json", {"provider": "groq", "methods": ["heuristic"], "rows": [good, bad]})
    out = collect_benchmarks(tmp_path)[0]
    assert out["runs"] == 1 and out["provider_errors"] == 1


def test_site_serves_static_pages(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("home", encoding="utf-8")
    (site / "console.html").write_text("console", encoding="utf-8")
    (site / "404.html").write_text("missing", encoding="utf-8")
    client = TestClient(create_app(runs_dir=tmp_path, static_dir=site, port=PORT), base_url=BASE)
    assert client.get("/").text == "home"
    assert client.get("/console").text == "console"
    assert client.head("/console").status_code == 200        # Next prefetches pages with HEAD
    prefetch = site / "console" / "__next.!abc" / "console"
    prefetch.mkdir(parents=True)
    (prefetch / "__PAGE__.txt").write_text("segment", encoding="utf-8")
    assert client.get("/console/__next.!abc.console.__PAGE__.txt?_rsc=x").text == "segment"
    res = client.get("/nope")
    assert res.status_code == 404 and res.text == "missing"
    assert client.get("/%2e%2e/secret").status_code == 404
    assert client.get("/api/nope").status_code == 404
