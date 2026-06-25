# Puppeteer++ — Setup & Run Guide

> Cross-platform instructions for **Linux / macOS (bash)** and **Windows (PowerShell)**.

---

## Prerequisites

| Requirement | Minimum version | Check command |
|---|---|---|
| Python | 3.10+ | `python --version` or `python3 --version` |
| pip | bundled with Python | `pip --version` |
| Git | any recent version | `git --version` |
| Groq API key | — | [console.groq.com](https://console.groq.com) (free) |

---

## 1. Clone the Repository

**Linux / macOS**
```bash
git clone <your-repo-url>
cd puppeteer-plus
```

**Windows (PowerShell)**
```powershell
git clone <your-repo-url>
cd puppeteer-plus
```

---

## 2. Create a Virtual Environment

Using a virtual environment keeps project dependencies isolated from your system Python.

**Linux / macOS**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell)**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> If PowerShell blocks the activation script, run this once and try again:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

You should see `(.venv)` at the start of your prompt when the environment is active.

---

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:

| Package | Purpose |
|---|---|
| `openai>=1.30.0` | Groq uses the OpenAI-compatible REST API — same SDK works |
| `python-dotenv>=1.0.0` | Auto-loads `.env` into `os.environ` at import time |
| `fastapi`, `uvicorn`, `websockets` | API server (used from Step 9 onward) |
| `numpy>=1.26.0` | Reward function & training (used from Step 5 onward) |
| `pytest>=8.0.0` | Test runner |
| `pytest-asyncio>=0.23.0` | Async test support for FastAPI tests |

---

## 4. Configure Your API Key

### 4.1 Get a Free Groq Key
1. Visit [https://console.groq.com](https://console.groq.com) → Sign up
2. Go to **API Keys** → **Create API Key**
3. Copy the key — it starts with `gsk_`

### 4.2 Create the `.env` File

**Linux / macOS**
```bash
cp .env.example .env
```

**Windows (PowerShell)**
```powershell
Copy-Item .env.example .env
```

Then open `.env` and replace the placeholder with your real key:

```env
GROQ_API_KEY=gsk_your_actual_key_here
```

> **The `.env` file is already listed in `.gitignore` — it will never be committed to git.**  
> You only do this once. From this point on, all scripts read the key automatically.

---

## 5. Verify the Setup

### 5.1 Offline Unit Tests (no API key needed)

These tests use a mock client and run entirely offline. They verify all logic — parsing, config, prompt building, episode history — without spending any API credits.

**Linux / macOS**
```bash
pytest tests/test_agent.py -v
```

**Windows (PowerShell)**
```powershell
pytest tests/test_agent.py -v
```

**Expected result:**
```
========================= test session starts ==========================
collected 33 items

tests/test_agent.py::TestAgentConfig::test_config_stores_all_fields       PASSED
tests/test_agent.py::TestAgentConfig::test_config_defaults                 PASSED
...
tests/test_agent.py::TestReasoningAgentLive::test_live_invoke_full_pipeline SKIPPED

========================= 32 passed, 1 skipped =========================
```

✅ **32 passed, 1 skipped = everything is set up correctly.**

---

### 5.2 Live End-to-End Test (makes a real Groq API call)

Sends an actual HTTP request to Groq, runs the full `invoke()` pipeline, and checks the model returns the correct answer.

**Linux / macOS**
```bash
pytest tests/test_agent.py -v --live -s
```

**Windows (PowerShell)**
```powershell
pytest tests/test_agent.py -v --live -s
```

> The `-s` flag streams `print()` output so you can see the raw model response.

**Expected output (values will vary slightly):**
```
============================================================
  LIVE TEST RESULT
============================================================
  Model         : llama-3.1-8b-instant
  Total tokens  : ~140-250
  Latency (ms)  : ~300-800
  Reasoning     : Time = 450 / 120 = 3.75 hours = 225 minutes.
  Final Answer  : 225 minutes
============================================================
PASSED
```

---

### 5.3 Smoke Test via `__main__`

Runs the script directly — a quick sanity check without pytest.

**Linux / macOS**
```bash
python3 core/agent.py
```

**Windows (PowerShell)**
```powershell
python core/agent.py
```

**Expected output:**
```
============================================================
  Puppeteer++ -- core/agent.py smoke test
============================================================

[OK] GroqClient created (base_url=https://api.groq.com/openai/v1)
[OK] Agent created: ReasoningAgent(model='llama-3.1-8b-instant', tools=[])

Task: If a train travels at 120 km/h and needs to cover 450 km, how many minutes will the journey take?
...

  Agent Name       : ReasoningAgent
  Latency (ms)     : 512.3
  Token Usage      : {"prompt_tokens": 120, "completion_tokens": 67, "total_tokens": 187}

  Final Answer     :
    225 minutes

[PASS] Smoke test passed. core/agent.py is working correctly.
```

---

## 6. Common Commands Reference

| Action | Linux / macOS | Windows (PowerShell) |
|---|---|---|
| Activate venv | `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| Deactivate venv | `deactivate` | `deactivate` |
| Install deps | `pip install -r requirements.txt` | `pip install -r requirements.txt` |
| Run all tests (offline) | `pytest tests/test_agent.py -v` | `pytest tests/test_agent.py -v` |
| Run live test | `pytest tests/test_agent.py -v --live -s` | `pytest tests/test_agent.py -v --live -s` |
| Smoke test | `python3 core/agent.py` | `python core/agent.py` |
| Run one test class | `pytest tests/test_agent.py::TestParseOutput -v` | same |
| Run one specific test | `pytest tests/test_agent.py::TestReasoningAgentUnit::test_invoke_parses_final_answer -v` | same |

---

## 7. Troubleshooting

### `GROQ_API_KEY not found`

You likely haven't created the `.env` file yet, or the key value is still the placeholder.

```
GROQ_API_KEY=gsk_your_actual_key_here   ← replace this with your real key
```

Alternatively, you can set it temporarily in the shell (not recommended long-term):

**Linux / macOS**
```bash
export GROQ_API_KEY="gsk_..."
```

**Windows (PowerShell)**
```powershell
$env:GROQ_API_KEY = "gsk_..."
```

---

### `ModuleNotFoundError: No module named 'core'`

You must run pytest from **inside** the `puppeteer-plus/` directory, not from a parent folder:

```bash
cd puppeteer-plus
pytest tests/test_agent.py -v
```

---

### `RateLimitError` during live test

Groq's free tier allows ~30 requests/min for the 8B model. `GroqClient` retries automatically up to 3 times with exponential back-off (2 s → 4 s → 8 s). If all retries fail, wait 60 seconds and run again.

---

### `openai.AuthenticationError`

Your key is invalid or has been revoked. Generate a new one at [https://console.groq.com](https://console.groq.com).

---

### `UnicodeEncodeError` in PowerShell (Windows only)

PowerShell may default to a non-UTF-8 encoding. Fix it for the current session:

```powershell
$OutputEncoding = [System.Text.Encoding]::UTF8
python core/agent.py
```

For a permanent fix, add this to your PowerShell profile (`$PROFILE`).

---

### Activation script blocked on Windows

```powershell
# Run once to allow local scripts:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
# Then activate normally:
.\.venv\Scripts\Activate.ps1
```

---

## 8. Project Structure (Step 1 state)

```
puppeteer-plus/
├── core/
│   ├── __init__.py          ← makes core/ a Python package
│   └── agent.py             ← BUILT: BaseAgent, GroqClient, AgentOutput, ReasoningAgent
├── tests/
│   ├── __init__.py
│   └── test_agent.py        ← BUILT: 32 unit tests + 1 live integration test
├── agents/                  ← pending (Steps 7+): 9 concrete agent types
├── api/                     ← pending (Step 9): FastAPI + WebSocket server
├── benchmarks/              ← pending (Step 11): GSM-Hard / MMLU-Pro runner
├── configs/                 ← pending: YAML experiment configs
├── dashboard/               ← pending (Step 10): React visualisation
├── extensions/              ← pending (Step 8): 3 original contributions
├── tools/                   ← pending: run_python, web_search, file_reader
├── training/                ← pending (Steps 5–6): reward.py, reinforce.py
├── .env                     ← YOU CREATE THIS — never commit to git
├── .env.example             ← safe template to share
├── .gitignore
├── requirements.txt
├── BUILD.md                 ← detailed step-by-step test verification guide
└── SETUP.md                 ← this file
```
