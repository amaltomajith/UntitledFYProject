"""Offline tests for update checks, one-click updates and the `compactcode` launcher. No network: fetch is faked."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from puppeteer_code import __version__, cli, paths, updates


def release(version="9.9.9", wheel=True, sums=True, **extra):
    assets = []
    if wheel:
        assets.append({"name": f"compactcode-{version}-py3-none-any.whl",
                       "browser_download_url": f"https://example.test/compactcode-{version}-py3-none-any.whl"})
    if sums:
        assets.append({"name": "SHA256SUMS", "browser_download_url": "https://example.test/SHA256SUMS"})
    return {"tag_name": f"v{version}", "body": "What changed", "html_url": "https://example.test/r",
            "assets": assets, "published_at": "2026-11-05T00:00:00Z", **extra}


@pytest.fixture
def feed(monkeypatch):
    """Serve fake GitHub responses: feed['release'] is the JSON, feed['files'] maps URL to bytes."""
    data = {"release": release(), "files": {}, "calls": 0}

    def fake_get(url, timeout=updates.TIMEOUT):
        data["calls"] += 1
        if url in data["files"]:
            return data["files"][url]
        if isinstance(data["release"], Exception):
            raise data["release"]
        return json.dumps(data["release"]).encode()

    monkeypatch.setattr(updates, "_get", fake_get)
    monkeypatch.delenv(updates.ENV_OFF, raising=False)
    monkeypatch.setattr(updates, "install_kind", lambda *a, **k: "uv-tool")
    return data


# ── versions ──

def test_version_compare():
    assert updates.is_newer("0.2.1", "0.2.0")
    assert updates.is_newer("0.10.0", "0.9.9")            # numeric, not text
    assert updates.is_newer("v1.0", "0.9.9")
    assert not updates.is_newer("0.2.0", "0.2.0")
    assert not updates.is_newer("0.2", "0.2.0")
    assert not updates.is_newer("0.1.9", "0.2.0")
    assert not updates.is_newer("0.3.0rc1", "0.2.0")      # pre-releases are never offered
    assert not updates.is_newer("garbage", "0.2.0")


def test_drafts_and_prereleases_are_ignored(feed, tmp_path):
    feed["release"] = release(prerelease=True)
    s = updates.check(force=True, home=tmp_path)
    assert not s["available"] and "not a final release" in s["error"]


# ── checks and the cache ──

def test_check_finds_update_and_caches(feed, tmp_path):
    s = updates.check(force=True, home=tmp_path, now=1000.0)
    assert s["available"] and s["latest"] == "9.9.9" and s["current"] == __version__
    assert s["notes"] == "What changed" and s["error"] is None
    cached = json.loads((tmp_path / "update.json").read_text())
    assert cached["checked_at"] == 1000.0 and cached["wheel_url"].endswith(".whl")


def test_automatic_check_at_most_once_a_day(feed, tmp_path):
    updates.check(home=tmp_path, now=1000.0)
    assert feed["calls"] == 1
    updates.check(home=tmp_path, now=1000.0 + updates.CHECK_EVERY - 1)
    assert feed["calls"] == 1                               # cached
    updates.check(home=tmp_path, now=1000.0 + updates.CHECK_EVERY)
    assert feed["calls"] == 2
    updates.check(force=True, home=tmp_path, now=1000.0 + updates.CHECK_EVERY + 1)
    assert feed["calls"] == 3                               # a manual check always asks


def test_private_mode_never_checks_automatically(feed, tmp_path):
    s = updates.check(home=tmp_path, private=True, now=1000.0)
    assert feed["calls"] == 0 and s["auto_blocked"] == "private"
    assert updates.refresh_in_background(tmp_path, private=True) is False
    updates.check(force=True, home=tmp_path, private=True)  # the button still works
    assert feed["calls"] == 1


def test_env_and_setting_turn_automatic_checks_off(feed, tmp_path, monkeypatch):
    updates.set_auto(False, home=tmp_path)
    updates.check(home=tmp_path, now=1000.0)
    assert feed["calls"] == 0 and updates.state(tmp_path)["auto"] is False
    updates.set_auto(True, home=tmp_path)
    monkeypatch.setenv(updates.ENV_OFF, "1")
    updates.check(home=tmp_path, now=1000.0)
    assert feed["calls"] == 0 and updates.state(tmp_path)["auto_blocked"] == "env"


def test_offline_is_an_error_not_a_crash(feed, tmp_path):
    feed["release"] = OSError("no network")
    s = updates.check(force=True, home=tmp_path)
    assert not s["available"] and "Could not reach GitHub" in s["error"]


def test_notice_line(feed, tmp_path):
    assert updates.notice(home=tmp_path, private=True) is None          # nothing cached yet
    updates.check(force=True, home=tmp_path)
    line = updates.notice(home=tmp_path)
    assert "9.9.9 is available" in line and updates.UPDATE_COMMAND in line


def test_corrupt_cache_is_ignored(tmp_path):
    (tmp_path / "update.json").write_text("{not json")
    assert updates.state(tmp_path)["latest"] is None


# ── install kind ──

def test_install_kind(tmp_path, monkeypatch):
    assert updates.install_kind(root=paths.ROOT) == "source"           # this repository
    other = tmp_path / "site-packages"
    other.mkdir()
    uv = str(Path.home() / "AppData" / "Roaming" / "uv" / "tools" / "compactcode")
    assert updates.install_kind(prefix=uv, root=other) == "uv-tool"
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    assert updates.install_kind(prefix=str(tmp_path / "venv"), root=other) == "pip"
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "tools"))
    assert updates.install_kind(prefix=str(tmp_path / "tools" / "compactcode"), root=other) == "uv-tool"


# ── download and apply ──

def _publish_wheel(feed, blob=b"wheel bytes", listed=None):
    name = "compactcode-9.9.9-py3-none-any.whl"
    feed["files"][f"https://example.test/{name}"] = blob
    digest = listed or hashlib.sha256(blob).hexdigest()
    feed["files"]["https://example.test/SHA256SUMS"] = f"{digest}  {name}\n".encode()


def test_download_verifies_checksum(feed, tmp_path):
    _publish_wheel(feed)
    updates.check(force=True, home=tmp_path)
    path = updates.download(home=tmp_path)
    assert path.read_bytes() == b"wheel bytes" and path.parent == tmp_path / "updates"


def test_download_refuses_a_mismatch(feed, tmp_path):
    _publish_wheel(feed, listed="0" * 64)
    updates.check(force=True, home=tmp_path)
    with pytest.raises(updates.UpdateError, match="does not match"):
        updates.download(home=tmp_path)
    assert not (tmp_path / "updates").exists()


def test_download_refuses_without_sums(feed, tmp_path):
    feed["release"] = release(sums=False)
    updates.check(force=True, home=tmp_path)
    with pytest.raises(updates.UpdateError, match="cannot be verified"):
        updates.download(home=tmp_path)


def test_apply_refused_when_busy_or_from_source(feed, tmp_path, monkeypatch):
    _publish_wheel(feed)
    updates.check(force=True, home=tmp_path)
    spawned = []
    with pytest.raises(updates.UpdateError, match="current request"):
        updates.apply(busy=True, home=tmp_path, spawn=lambda c, d: spawned.append(c))
    monkeypatch.setattr(updates, "install_kind", lambda *a, **k: "source")
    with pytest.raises(updates.UpdateError, match="git pull"):
        updates.apply(home=tmp_path, spawn=lambda c, d: spawned.append(c))
    assert spawned == []


def test_apply_refused_with_no_update(feed, tmp_path):
    feed["release"] = release(version=__version__)
    updates.check(force=True, home=tmp_path)
    with pytest.raises(updates.UpdateError, match="No newer version"):
        updates.apply(home=tmp_path, spawn=lambda c, d: None)


def test_apply_hands_over_to_a_helper(feed, tmp_path, monkeypatch):
    _publish_wheel(feed)
    updates.check(force=True, home=tmp_path)
    monkeypatch.setattr(updates, "find_uv", lambda: "/x/uv")
    spawned = []
    info = updates.apply(home=tmp_path, spawn=lambda c, d: spawned.append(c))
    assert info["to"] == "9.9.9" and Path(info["log"]).exists()
    joined = " ".join(spawned[0])
    assert "tool" in joined and "--force" in joined and "compactcode[web] @ file:" in joined


def test_uv_tool_update_needs_uv(tmp_path):
    with pytest.raises(updates.UpdateError, match="uv was not found"):
        updates.install_args("uv-tool", tmp_path / "w.whl", None, "python")
    pip = updates.install_args("pip", tmp_path / "w.whl", None, "python")
    assert pip[:4] == ["python", "-m", "pip", "install"]


def test_helper_waits_then_installs_then_relaunches(tmp_path):
    install = ["uv", "tool", "install", "--force", "compactcode[web] @ file:///C:/it's.whl"]
    win = updates.helper_command(install, [11, 22], tmp_path / "u.log", ["C:/bin/compactcode.exe"], windows=True)
    script = win[-1]
    assert win[0] == "powershell" and "Wait-Process -Id 11,22" in script
    assert script.index("Wait-Process") < script.index("'tool'") < script.index("Start-Process")
    assert "it''s.whl" in script                              # quoted for PowerShell
    unix = updates.helper_command(install, [11], tmp_path / "u.log", None, windows=False)
    assert unix[:2] == ["/bin/sh", "-c"] and "kill -0 11" in unix[2] and "nohup" not in unix[2]
    assert "it'\"'\"'s.whl" in unix[2]                         # quoted for sh


# ── paths ──

def test_site_dir_falls_back_to_the_bundled_copy(tmp_path):
    root, package = tmp_path / "root", tmp_path / "pkg"
    assert paths.site_dir(root, package) == package / "site"
    assert not paths.site_ready(root, package)
    (package / "site").mkdir(parents=True)
    (package / "site" / "ide.html").write_text("x")
    assert paths.site_ready(root, package)
    (root / "web" / "out").mkdir(parents=True)
    (root / "web" / "out" / "ide.html").write_text("x")
    assert paths.site_dir(root, package) == root / "web" / "out"   # a built checkout wins


def test_runs_dir(tmp_path):
    assert paths.runs_dir(paths.ROOT) == paths.ROOT / "runs"
    assert paths.runs_dir(tmp_path, home=tmp_path / "h") == tmp_path / "h" / ".pcode" / "runs"


# ── the compactcode command ──

def test_launch_opens_the_ide_in_a_project_folder(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    assert cli.launch_argv([], project, home=tmp_path) == ["ide", "--folder", str(project)]
    assert cli.launch_argv(["--no-browser"], project, home=tmp_path) == ["ide", "--no-browser", "--folder", str(project)]
    assert cli.launch_argv(["--folder", "x"], project, home=tmp_path) == ["ide", "--folder", "x"]


def test_launch_skips_home_and_drive_root(tmp_path):
    assert cli.launch_argv([], tmp_path, home=tmp_path) == ["ide"]
    root = Path(tmp_path.anchor)
    assert cli.launch_argv([], root, home=tmp_path) == ["ide"]


def test_launch_passes_commands_through(tmp_path):
    assert cli.launch_argv(["update", "--check"], tmp_path / "p", home=tmp_path) == ["update", "--check"]
    assert cli.launch_argv(["chat"], tmp_path, home=tmp_path) == ["chat"]
    assert cli.launch_argv(["--version"], tmp_path, home=tmp_path) == ["--version"]


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0 and __version__ in capsys.readouterr().out


def test_free_port_skips_a_taken_one():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        taken = s.getsockname()[1]
        assert cli.free_port(taken, tries=5) not in (None, taken)


# ── IDE routes ──

@pytest.fixture
def ide(tmp_path, feed):
    from fastapi.testclient import TestClient

    from puppeteer_code.assistant.ide import IdeHub
    from puppeteer_code.providers import ProviderSession
    from puppeteer_code.server import create_app

    token = "t" * 32
    hub = IdeHub(token, lambda name: ProviderSession("mock", responder=lambda m, msgs: "Done."),
                 home=tmp_path / "home", provider="mock")
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=hub)
    client = TestClient(app, base_url="http://127.0.0.1:8765",
                        headers={"host": "127.0.0.1:8765", "x-pcode-token": token})
    yield client, hub, app
    hub.close()


def test_update_routes_need_the_token(ide):
    client, _, _ = ide
    assert client.get("/api/ide/update", headers={"x-pcode-token": "wrong"}).status_code == 401


def test_update_check_route(ide, feed):
    client, hub, _ = ide
    s = client.post("/api/ide/update/check").json()
    assert s["available"] and s["latest"] == "9.9.9" and s["can_apply"] and s["blocked"] is None
    assert (hub.home / "update.json").exists()
    assert client.get("/api/ide/update").json()["latest"] == "9.9.9"


def test_update_settings_route(ide):
    client, hub, _ = ide
    assert client.put("/api/ide/update/settings", json={"auto": False}).json()["auto"] is False
    assert updates.state(hub.home)["auto"] is False


def test_update_apply_refused_while_busy(ide, feed, monkeypatch):
    client, hub, _ = ide
    client.post("/api/ide/update/check")
    hub.running_command = True
    s = client.get("/api/ide/update").json()
    assert s["blocked"] == "busy" and not s["can_apply"]
    called = []
    monkeypatch.setattr(updates, "apply", lambda **k: called.append(k))
    reply = client.post("/api/ide/update/apply")
    assert reply.status_code == 409 and reply.json()["detail"]["code"] == "busy" and called == []
    hub.running_command = False


def test_update_apply_stops_the_server(ide, feed, monkeypatch):
    client, _, app = ide

    class FakeServer:
        should_exit = False

    app.state.server = FakeServer()
    from puppeteer_code.assistant import ide as ide_module
    forced = []
    monkeypatch.setattr(ide_module, "UPDATE_EXIT_AFTER", 0.2)
    monkeypatch.setattr(ide_module, "_force_exit", lambda hub: forced.append(hub))
    client.post("/api/ide/update/check")
    monkeypatch.setattr(updates, "apply", lambda **k: {"ok": True, "from": __version__, "to": "9.9.9", "log": "x"})
    reply = client.post("/api/ide/update/apply")
    assert reply.status_code == 202 and reply.json()["to"] == "9.9.9"
    import time
    deadline = time.time() + 5
    while not app.state.server.should_exit and time.time() < deadline:
        time.sleep(0.05)
    assert app.state.server.should_exit
    deadline = time.time() + 5
    while not forced and time.time() < deadline:
        time.sleep(0.05)
    assert forced                                            # the watchdog would end a process that did not exit


def test_update_apply_refusal_is_a_409(ide, feed, monkeypatch):
    client, _, _ = ide
    client.post("/api/ide/update/check")

    def refuse(**k):
        raise updates.UpdateError("This copy runs from a git checkout. Update it with git pull.")
    monkeypatch.setattr(updates, "apply", refuse)
    reply = client.post("/api/ide/update/apply")
    assert reply.status_code == 409 and "git pull" in reply.json()["detail"]["message"]


def test_helper_breaks_away_from_the_job_when_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(updates.os, "name", "nt")
    monkeypatch.setattr(updates.subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False)
    monkeypatch.setattr(updates.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    calls = []

    def popen(cmd, creationflags=0, **kw):
        calls.append(creationflags)
        if creationflags & updates.BREAKAWAY and len(calls) == 1 and getattr(popen, "deny", False):
            raise PermissionError("job forbids breakaway")

    updates.spawn_detached(["x"], tmp_path, popen=popen)
    assert calls == [0x8000000 | 0x200 | updates.BREAKAWAY]
    calls.clear()
    popen.deny = True
    updates.spawn_detached(["x"], tmp_path, popen=popen)
    assert calls == [0x8000000 | 0x200 | updates.BREAKAWAY, 0x8000000 | 0x200]   # falls back inside the job


def test_waits_for_this_process():
    assert updates.processes_to_wait_for()[0] == __import__("os").getpid()
