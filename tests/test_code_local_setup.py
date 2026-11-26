"""Offline tests for setting up a model on this computer: hardware, the model list, download and install.
A fake Ollama client and a fake installer stand in for the real ones; nothing is downloaded or installed."""

import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from puppeteer_code import hardware, local_models
from puppeteer_code.assistant import local_setup
from puppeteer_code.assistant.ide import IdeHub
from puppeteer_code.assistant.local_setup import DOWNLOADING, LocalSetup
from puppeteer_code.hardware import GB
from puppeteer_code.ollama import Cancelled
from puppeteer_code.privacy import PrivacyError
from puppeteer_code.providers import ProviderSession
from puppeteer_code.server import create_app

TOKEN = "test-token-123"
AUTH = {"host": "127.0.0.1:8765", "x-pcode-token": TOKEN}


# ── hardware ─────────────────────────────────────────────────────────────────

def smi(stdout, code=0):
    return lambda *a, **k: SimpleNamespace(returncode=code, stdout=stdout)


def test_nvidia_memory_is_read_not_guessed():
    hw = hardware.detect(run=smi("NVIDIA GeForce RTX 4050 Laptop GPU, 6141\n"), memory=16 * GB, disk=100 * GB,
                         apple=False)
    assert hw["gpu"] == {"name": "NVIDIA GeForce RTX 4050 Laptop GPU", "memory": 6141 * 1024 * 1024, "shared": False}
    assert hw["memory"] == 16 * GB and hw["disk_free"] == 100 * GB


def test_no_nvidia_means_no_gpu_number():
    def missing(*a, **k):
        raise FileNotFoundError("nvidia-smi")
    assert hardware.detect(run=missing, memory=8 * GB, disk=1, apple=False)["gpu"] is None
    assert hardware.detect(run=smi("", 9), memory=8 * GB, disk=1, apple=False)["gpu"] is None


def test_apple_silicon_shares_memory():
    hw = hardware.detect(run=smi(""), memory=16 * GB, disk=1, apple=True)
    assert hw["gpu"]["shared"] is True and hw["gpu"]["memory"] == 16 * GB


def test_nvidia_smi_is_called_without_stdin():
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="")
    hardware.nvidia_gpus(run)
    assert seen["stdin"] is subprocess.DEVNULL and seen["timeout"]


# ── the model list ───────────────────────────────────────────────────────────

def test_fit_labels():
    nine = local_models.BY_NAME["qwen3.5:9b"]
    big_gpu = {"memory": 32 * GB, "gpu": {"memory": 24 * GB, "shared": False}}
    small_gpu = {"memory": 16 * GB, "gpu": {"memory": 6 * GB, "shared": False}}
    assert local_models.fit(nine, big_gpu) == "gpu"
    assert local_models.fit(nine, small_gpu) == "split"
    assert local_models.fit(nine, {"memory": 16 * GB, "gpu": None}) == "cpu"
    assert local_models.fit(nine, {"memory": 8 * GB, "gpu": None}) == "too_big"
    assert local_models.fit(nine, {"memory": None, "gpu": None}) == "unknown"


def test_recommendation_fits_the_card_or_stays_small_for_the_processor():
    assert local_models.recommend({"memory": 32 * GB, "gpu": {"memory": 24 * GB, "shared": False}}) == "qwen3-coder:30b"
    assert local_models.recommend({"memory": 16 * GB, "gpu": {"memory": 12 * GB, "shared": False}}) == "qwen3.5:9b"
    # An 8 GB card (the owner's): the tested model runs partly on it, so it beats a smaller untested one.
    assert local_models.fit(next(m for m in local_models.CATALOG if m.name == "qwen3.5:9b"),
                            {"memory": 16 * GB, "gpu": {"memory": 8 * GB, "shared": False}}) == "split"
    assert local_models.recommend({"memory": 16 * GB, "gpu": {"memory": 8 * GB, "shared": False}}) == "qwen3.5:9b"
    cpu_only = local_models.recommend({"memory": 16 * GB, "gpu": None})
    assert local_models.BY_NAME[cpu_only].size <= 5
    assert local_models.recommend({"memory": 2 * GB, "gpu": None}) is None


def test_only_the_tested_model_says_tested():
    assert [m.name for m in local_models.CATALOG if m.tested] == ["qwen3.5:9b"]
    assert not any(local_models.is_cloud_name(m.name) for m in local_models.CATALOG)


def test_installed_models_are_read_from_manifests(tmp_path):
    root = tmp_path / "manifests" / "registry.ollama.ai"
    (root / "library" / "qwen3.5").mkdir(parents=True)
    (root / "library" / "qwen3.5" / "9b").write_text("{}")
    (root / "someone" / "tool").mkdir(parents=True)
    (root / "someone" / "tool" / "latest").write_text("{}")
    (root / "library" / "gpt-oss").mkdir(parents=True)
    (root / "library" / "gpt-oss" / "120b-cloud").write_text("{}")
    assert local_models.installed(tmp_path) == ["qwen3.5:9b", "someone/tool:latest"]
    assert local_models.installed(tmp_path / "nothing") == []


def test_choice_is_saved_and_never_a_cloud_model(tmp_path):
    assert local_models.chosen(tmp_path) is None
    local_models.choose(tmp_path, "qwen3:8b")
    assert local_models.chosen(tmp_path) == "qwen3:8b"
    with pytest.raises(ValueError):
        local_models.choose(tmp_path, "gpt-oss:120b-cloud")
    (tmp_path / "local.json").write_text('{"model": "kimi-k2:cloud"}')
    assert local_models.chosen(tmp_path) is None


def test_the_chosen_model_is_used_first(tmp_path):
    local_models.choose(tmp_path, "qwen3:8b")
    session = ProviderSession("mock", home=tmp_path)
    tiers = session._local_tiers({"tiers": {"quality": ["qwen3.5:9b"]}},
                                 SimpleNamespace(list_models=lambda: ["qwen3.5:9b", "qwen3:8b"]))
    assert tiers == {"fast": "qwen3:8b", "quality": "qwen3:8b"}
    tiers = session._local_tiers({"tiers": {"quality": ["qwen3.5:9b"]}},
                                 SimpleNamespace(list_models=lambda: ["qwen3.5:9b"]))   # chosen one was removed
    assert tiers["quality"] == "qwen3.5:9b"
    with pytest.raises(PrivacyError, match="Download one in the IDE's Settings"):
        session._local_tiers({"tiers": {"quality": ["qwen3.5:9b"]}}, SimpleNamespace(list_models=lambda: []))


# ── download ─────────────────────────────────────────────────────────────────

class FakeOllama:
    def __init__(self, steps=None, gate=None, error=None):
        self.steps = steps or [("pulling manifest", 0, 0), ("pulling abc", 50, 100), ("pulling abc", 100, 100),
                               ("success", 100, 100)]
        self.gate, self.error = gate, error
        self.cancel = None
        self.pulled = []

    def pull(self, model, on_progress):
        self.pulled.append(model)
        for status, done, total in self.steps:
            if self.gate is not None:
                self.gate.wait(2)
            if self.cancel is not None and self.cancel.is_set():
                raise Cancelled()
            on_progress(status, done, total)
        if self.error:
            raise RuntimeError(self.error)


def setup_for(tmp_path, client=None, busy=lambda: False, found="ollama"):
    events = []
    setup = LocalSetup(tmp_path / "home", lambda e, keep=True: events.append((e, keep)), busy=busy,
                       make_client=lambda: client or FakeOllama(), detect=lambda: {"memory": 16 * GB, "gpu": None},
                       find=lambda: found, models_folder=tmp_path / "models")
    return setup, events


def wait_for(predicate, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_a_download_reports_progress_and_becomes_the_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "free_disk", lambda folder=None: 100 * GB)
    fake = FakeOllama()
    setup, events = setup_for(tmp_path, fake)
    started = setup.start_pull("qwen3.5:9b")
    assert started["message"] == DOWNLOADING
    wait_for(lambda: events and events[-1][1] is True)      # the stored event is the last one sent
    assert setup.pull["state"] == "done" and setup.pull["done"] == 100 and fake.pulled == ["qwen3.5:9b"]
    final, kept = events[-1]
    assert final["type"] == "local_pull" and final["state"] == "done" and kept is True
    assert all(keep is False for e, keep in events[:-1])        # progress is transient
    assert local_models.chosen(tmp_path / "home") == "qwen3.5:9b"


def test_a_second_download_does_not_change_the_choice(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "free_disk", lambda folder=None: 100 * GB)
    local_models.choose(tmp_path / "home", "qwen3:8b")
    setup, _ = setup_for(tmp_path)
    setup.start_pull("qwen3:4b")
    wait_for(lambda: setup.pull["state"] != "running")
    assert local_models.chosen(tmp_path / "home") == "qwen3:8b"


def test_a_download_can_be_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "free_disk", lambda folder=None: 100 * GB)
    gate = threading.Event()
    setup, _ = setup_for(tmp_path, FakeOllama(gate=gate))
    setup.start_pull("qwen3:4b")
    with pytest.raises(RuntimeError, match="already running"):
        setup.start_pull("qwen3:8b")
    assert setup.cancel_pull() is True
    gate.set()
    wait_for(lambda: setup.pull["state"] != "running")
    assert setup.pull["state"] == "cancelled" and local_models.chosen(tmp_path / "home") is None
    assert setup.cancel_pull() is False


def test_a_failed_download_says_why_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "free_disk", lambda folder=None: 100 * GB)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-hidden-value-999")
    setup, _ = setup_for(tmp_path, FakeOllama(error="pull model manifest: file does not exist sk-hidden-value-999"))
    setup.start_pull("qwen3:4b")
    wait_for(lambda: setup.pull["state"] != "running")
    assert setup.pull["state"] == "failed" and "file does not exist" in setup.pull["message"]
    assert "sk-hidden-value-999" not in setup.pull["message"]


@pytest.mark.parametrize("model,error", [("gpt-oss:120b-cloud", PrivacyError), ("kimi-k2:cloud", PrivacyError),
                                         ("bad name; rm -rf", ValueError)])
def test_refused_names(tmp_path, model, error):
    setup, _ = setup_for(tmp_path)
    with pytest.raises(error):
        setup.start_pull(model)


def test_no_download_during_a_request_or_without_ollama(tmp_path):
    setup, _ = setup_for(tmp_path, busy=lambda: True)
    with pytest.raises(RuntimeError, match="never run during"):
        setup.start_pull("qwen3:4b")
    setup, _ = setup_for(tmp_path, found=None)
    with pytest.raises(PrivacyError, match="Install Ollama"):
        setup.start_pull("qwen3:4b")


def test_no_download_onto_a_full_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "free_disk", lambda folder=None: 2 * GB)
    setup, _ = setup_for(tmp_path)
    with pytest.raises(OSError, match="Not enough free disk"):
        setup.start_pull("qwen3.5:9b")


def test_choose_needs_an_installed_model(tmp_path):
    setup, _ = setup_for(tmp_path)
    with pytest.raises(LookupError):
        setup.choose("qwen3:8b")
    tag = tmp_path / "models" / "manifests" / "registry.ollama.ai" / "library" / "qwen3"
    tag.mkdir(parents=True)
    (tag / "8b").write_text("{}")
    setup.choose("qwen3:8b")
    assert local_models.chosen(tmp_path / "home") == "qwen3:8b"


# ── install ──────────────────────────────────────────────────────────────────

class FakeInstaller:
    def __init__(self, lines, code=0):
        self.stdout = iter(line + "\n" for line in lines)
        self.code = code

    def wait(self):
        return self.code


def test_install_on_windows_streams_its_output(tmp_path, monkeypatch):
    monkeypatch.setattr(local_setup.sys, "platform", "win32")
    found = []
    setup, events = setup_for(tmp_path, found=None)
    setup.find = lambda: found[0] if found else None
    setup.start_installer = lambda: found.append("ollama.exe") or FakeInstaller([">>> Downloading", ">>> Installed"])
    setup.run_install()
    wait_for(lambda: events and events[-1][1] is True)
    assert setup.install["state"] == "done" and setup.install["lines"] == [">>> Downloading", ">>> Installed"]
    assert events[-1][0] == {"type": "local_install", "state": "done", "code": 0, "found": True}


def test_a_failed_install_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(local_setup.sys, "platform", "win32")
    setup, _ = setup_for(tmp_path, found=None)
    setup.start_installer = lambda: FakeInstaller(["error"], code=1)
    setup.run_install()
    wait_for(lambda: setup.install["state"] != "running")
    assert setup.install["state"] == "failed" and setup.install["code"] == 1


def test_elsewhere_the_person_runs_the_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(local_setup.sys, "platform", "linux")
    setup, _ = setup_for(tmp_path)
    assert setup.status()["install"]["command"] == "curl -fsSL https://ollama.com/install.sh | sh"
    with pytest.raises(PermissionError):
        setup.run_install()


def test_the_official_installer_is_the_one_run():
    assert local_setup.WINDOWS_INSTALL == "irm https://ollama.com/install.ps1 | iex"


# ── routes ───────────────────────────────────────────────────────────────────

def ide_client(tmp_path):
    hub = IdeHub(TOKEN, lambda name: ProviderSession("mock", responder=lambda m, msgs: "done"),
                 home=tmp_path / "home", provider="mock")
    app = create_app(runs_dir=tmp_path / "runs", static_dir=tmp_path / "out", ide=hub)
    setup = app.state.local_setup
    setup.make_client, setup.find = FakeOllama, (lambda: "ollama")
    setup.detect = lambda: {"memory": 16 * GB, "gpu": None, "disk_free": 100 * GB}
    setup.folder = tmp_path / "models"
    return TestClient(app, base_url="http://127.0.0.1:8765"), hub, setup


def test_local_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(local_setup, "ollama_version", lambda binary: "0.30.1")
    monkeypatch.setattr(hardware, "free_disk", lambda folder=None: 100 * GB)
    client, hub, setup = ide_client(tmp_path)
    assert client.get("/api/ide/local", headers={"host": "127.0.0.1:8765"}).status_code == 401
    body = client.get("/api/ide/local", headers=AUTH).json()
    assert body["ollama"] == {"found": True, "version": "0.30.1"}
    assert len(body["models"]) == len(local_models.CATALOG) and body["chosen"] is None
    assert sum(m["recommended"] for m in body["models"]) == 1
    assert client.post("/api/ide/local/pull", json={"model": "x:cloud"}, headers=AUTH).status_code == 422
    assert client.post("/api/ide/local/pull", json={"model": "qwen3:4b"}, headers=AUTH).status_code == 202
    wait_for(lambda: setup.pull["state"] != "running")
    assert client.get("/api/ide/local", headers=AUTH).json()["pull"]["state"] == "done"
    assert client.post("/api/ide/local/choose", json={"model": "qwen3:8b"}, headers=AUTH).status_code == 409
    assert client.post("/api/ide/local/cancel", headers=AUTH).json() == {"cancelled": False}


def test_downloads_wait_for_a_request(tmp_path, monkeypatch):
    monkeypatch.setattr(hardware, "free_disk", lambda folder=None: 100 * GB)
    client, hub, _ = ide_client(tmp_path)
    hub.running_command = True
    reply = client.post("/api/ide/local/pull", json={"model": "qwen3:4b"}, headers=AUTH)
    assert reply.status_code == 409 and "never run during" in reply.json()["detail"]["message"]
