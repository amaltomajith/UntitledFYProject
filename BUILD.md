# Puppeteer++ — Build & Test Guide
## `core/agent.py` — Step 1 Verification

> **Who this is for:** Amal Tom Ajith. This document tells you exactly what to run, in what order, and what to expect at every step.

---

## 1. One-Time Setup

### 1.1 Get your Groq API Key
1. Go to [https://console.groq.com](https://console.groq.com) → Sign up (free)
2. Click **API Keys** → **Create API Key**
3. Copy the key (starts with `gsk_`)

### 1.2 Create your `.env` file

```powershell
# From inside puppeteer-plus/
Copy-Item .env.example .env
```

Open `.env` and replace the placeholder:
```
GROQ_API_KEY=gsk_your_actual_key_here
```

> **Never commit `.env` to git.** It's already in `.gitignore`.

### 1.3 Install dependencies

```powershell
# From inside puppeteer-plus/
pip install -r requirements.txt
```

This installs:
| Package | Why |
|---|---|
| `openai>=1.30.0` | Groq uses the OpenAI REST format — same SDK |
| `python-dotenv>=1.0.0` | Loads `.env` into `os.environ` automatically |
| `pytest>=8.0.0` | Test runner |
| `pytest-asyncio>=0.23.0` | Needed for async tests later (FastAPI) |
| `numpy>=1.26.0` | Needed from `training/reward.py` onward |
| `fastapi`, `uvicorn`, `websockets` | Needed from `api/server.py` onward |

---

## 2. Project Structure (current state)

```
puppeteer-plus/
├── core/
│   ├── __init__.py        ← makes core/ a package
│   └── agent.py           ← BUILT -- BaseAgent, GroqClient, AgentOutput
├── tests/
│   ├── __init__.py
│   └── test_agent.py      ← BUILT -- 32 unit tests + 1 live test
├── .env                   ← YOU CREATE THIS (from .env.example)
├── .env.example           ← template
├── .gitignore
└── requirements.txt
```

---

## 3. Running Tests

### 3.1 Unit Tests (no API key needed — 100% offline)

These tests use a `MockGroqClient` that returns a canned response. They verify all the logic — parsing, config, state management, prompt building — without spending a single API credit.

```powershell
# Navigate to the project first
cd "f:\Research - FInal Year Thesis\SourceCode\puppeteer-plus"

# Run unit tests
pytest tests/test_agent.py -v
```

**Expected output:**

```
========================= test session starts ==========================
platform win32 -- Python 3.11.x
collected 33 items

tests/test_agent.py::TestAgentConfig::test_config_stores_all_fields      PASSED
tests/test_agent.py::TestAgentConfig::test_config_defaults                PASSED
tests/test_agent.py::TestAgentConfig::test_empty_tools_for_pure_reasoning PASSED
tests/test_agent.py::TestAgentOutput::test_output_is_dataclass            PASSED
tests/test_agent.py::TestAgentOutput::test_to_context_string_contains_agent_name PASSED
tests/test_agent.py::TestAgentOutput::test_to_context_string_contains_reasoning  PASSED
tests/test_agent.py::TestAgentOutput::test_to_context_string_contains_answer     PASSED
tests/test_agent.py::TestAgentOutput::test_metadata_defaults_to_empty_dict       PASSED
tests/test_agent.py::TestAgentOutput::test_two_outputs_dont_share_metadata       PASSED
tests/test_agent.py::TestParseOutput::test_parses_correct_format          PASSED
tests/test_agent.py::TestParseOutput::test_parses_multiline_reasoning     PASSED
tests/test_agent.py::TestParseOutput::test_graceful_degradation_no_tags   PASSED
tests/test_agent.py::TestParseOutput::test_graceful_degradation_no_final_answer_tag PASSED
tests/test_agent.py::TestParseOutput::test_case_insensitive               PASSED
tests/test_agent.py::TestParseOutput::test_empty_string                   PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_agent_name_is_class_name       PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_agent_has_no_tools             PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_invoke_returns_agent_output    PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_invoke_populates_agent_name    PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_invoke_populates_token_usage   PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_invoke_parses_reasoning_result PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_invoke_parses_final_answer     PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_invoke_stores_in_episode_history PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_reset_episode_clears_history   PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_context_is_passed_to_action_prompt PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_role_prompt_used_as_system_message PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_repr_is_informative            PASSED
tests/test_agent.py::TestReasoningAgentUnit::test_to_context_string_is_labelled  PASSED
tests/test_agent.py::TestGroqClientUnit::test_raises_without_api_key             PASSED
tests/test_agent.py::TestGroqClientUnit::test_accepts_key_as_argument            PASSED
tests/test_agent.py::TestGroqClientUnit::test_model_aliases_are_strings          PASSED
tests/test_agent.py::TestGroqClientUnit::test_base_url_is_groq                   PASSED
tests/test_agent.py::TestReasoningAgentLive::test_live_invoke_full_pipeline      SKIPPED

========================= 32 passed, 1 skipped in X.XXs =================
```

**32 passed, 1 skipped = SUCCESS. Move to 3.2.**

---

### 3.2 Live End-to-End Test (requires GROQ_API_KEY in `.env`)

This sends a real HTTP request to Groq, runs the full `invoke()` pipeline, and verifies the model correctly computes 225 minutes.

```powershell
# Add your key to .env first, then:
pytest tests/test_agent.py -v --live -s
```

> The `-s` flag shows `print()` output so you can see the actual model response.

**Expected LIVE TEST RESULT block:**

```
============================================================
  LIVE TEST RESULT
============================================================
  Model         : llama-3.1-8b-instant
  Total tokens  : ~140-250 (varies per call)
  Latency (ms)  : ~300-800 (varies by Groq server load)
  Reasoning     : The train travels at 120 km/h. Distance = 450 km.
                  Time = 450/120 = 3.75 hours. 3.75 * 60 = 225 minutes.
  Final Answer  : 225 minutes
============================================================
PASSED
```

---

### 3.3 Smoke test via `__main__`

Alternative to pytest — runs the script directly, useful for quick checking:

```powershell
# Set your key in PowerShell first:
$env:GROQ_API_KEY = "gsk_your_key"

# Then run the script:
python core/agent.py
```

**Expected output:**

```
============================================================
  Puppeteer++ -- core/agent.py smoke test
============================================================

12:xx:xx | INFO | [ReasoningAgent] Invoking model=llama-3.1-8b-instant ...
12:xx:xx | INFO | [ReasoningAgent] Done. tokens=187, latency=512ms

------------------------------------------------------------
  AgentOutput
------------------------------------------------------------
  Agent Name       : ReasoningAgent
  Model Used       : llama-3.1-8b-instant
  Latency (ms)     : 512.3
  Token Usage      : {"prompt_tokens": 120, "completion_tokens": 67, "total_tokens": 187}

  Reasoning Result :
    The train travels at 120 km/h over 450 km.
    Time = 450 / 120 = 3.75 hours.
    3.75 hours * 60 minutes/hour = 225 minutes.

  Final Answer     :
    225 minutes
------------------------------------------------------------

  Context string passed to next agent in the episode:
  ----------------------------------------
  [ReasoningAgent]
  Reasoning: The train travels at 120 km/h...
  Answer: 225 minutes
  ----------------------------------------

  Episode history length (before reset): 1
  Episode history length (after reset) : 0

[PASS] Smoke test passed. core/agent.py is working correctly.
```

---

## 4. What Each Test Proves (Paper Mapping)

| Test name | What it verifies | Paper §/eq |
|---|---|---|
| `test_config_stores_all_fields` | `(m, r, t)` triple stored correctly | §2, ¶1 |
| `test_invoke_returns_agent_output` | `f_{a_t}` mapping returns structured output | §2.1 |
| `test_invoke_populates_token_usage` | `C_t` step cost captured for reward fn | §2.2 |
| `test_invoke_stores_in_episode_history` | Trajectory `τ` bookkeeping | §2.2 reward eq |
| `test_reset_episode_clears_history` | Episodes isolated (no state leak between tasks) | §2.2 |
| `test_context_is_passed_to_action_prompt` | Serialised orchestration: agent sees prior outputs | §2.1 |
| `test_role_prompt_used_as_system_message` | `r` from `a=(m,r,t)` is the LLM system persona | Appendix B.2 |
| `test_parses_correct_format` | `REASONING RESULT` / `FINAL ANSWER` extraction | Appendix B.2, Fig 15-16 |
| `test_graceful_degradation_*` | Parser doesn't crash on malformed LLM output | Robustness |
| `test_two_outputs_dont_share_metadata` | Mutable dataclass default trap avoided | Python correctness |
| `test_live_invoke_full_pipeline` | Full API call → correct answer (225 min) | §3 GSM-Hard benchmark |

---

## 5. Architecture Summary

```
core/agent.py
|
+-- AgentConfig (dataclass)
|   +-- model_name   ->  m  from a = (m, r, t)   [Section 2]
|   +-- role_prompt  ->  r  from a = (m, r, t)   [Section 2, Appendix B.2]
|   +-- tools        ->  t  from a = (m, r, t)   [Section 2, Table 2]
|
+-- AgentOutput (dataclass)
|   +-- reasoning_result  -> REASONING RESULT: tag   [Appendix B.2, Fig 15-16]
|   +-- final_answer      -> FINAL ANSWER: tag        [Appendix B.2, Fig 15-16]
|   +-- token_usage       -> C_t for reward fn R(tau) [Section 2.2]
|   +-- episode_history   -> trajectory tau building  [Section 2.2]
|   +-- to_context_string() -> S_t update (Phi)       [Section 2.1]
|
+-- GroqClient
|   +-- Wraps OpenAI SDK at api.groq.com
|   +-- Exponential back-off on RateLimitError (2s, 4s, 8s)
|   +-- Returns plain dict (stable interface regardless of SDK version)
|
+-- BaseAgent (abstract)
    +-- invoke(task, context) -> AgentOutput   [f_{a_t} mapping, Section 2.1]
    +-- _build_messages()     -> [system, user] [role prompt + action prompt]
    +-- _parse_output()       -> (reasoning, answer)  [Appendix B.2 format]
    +-- reset_episode()       -> clears episode_history
    +-- _build_action_prompt() -> ABSTRACT (each agent implements its own)

    Concrete: ReasoningAgent
    Role prompt: "You are an expert in logical reasoning..." [Appendix B.2, Fig 13]
    Action prompt: "REASONING RESULT: ... FINAL ANSWER: ..." [Appendix B.2, Fig 15]
```

---

## 6. Troubleshooting

### `GROQ_API_KEY not found`
```powershell
# In PowerShell:
$env:GROQ_API_KEY = "gsk_..."
# OR create .env file with:  GROQ_API_KEY=gsk_...
```

### `ModuleNotFoundError: No module named 'core'`
Run pytest from **inside** the `puppeteer-plus/` directory:
```powershell
cd "f:\Research - FInal Year Thesis\SourceCode\puppeteer-plus"
pytest tests/test_agent.py -v
```

### `RateLimitError` during live test
Groq free tier: ~30 req/min for the 8B model. Wait 60 seconds and retry.
`GroqClient` has built-in back-off — it retries up to 3 times automatically.

### `openai.AuthenticationError`
Key is wrong or expired. Get a new one at [https://console.groq.com](https://console.groq.com).

### `UnicodeEncodeError` in PowerShell
```powershell
$OutputEncoding = [System.Text.Encoding]::UTF8
python core/agent.py
```

---

## 7. Quick Reference — All Commands

```powershell
# ----- SETUP -----
cd "f:\Research - FInal Year Thesis\SourceCode\puppeteer-plus"
Copy-Item .env.example .env          # then edit .env with your key
pip install -r requirements.txt

# ----- SET KEY (PowerShell) -----
$env:GROQ_API_KEY = "gsk_your_key_here"

# ----- TEST (offline, no key needed) -----
pytest tests/test_agent.py -v

# ----- TEST (live Groq call) -----
pytest tests/test_agent.py -v --live -s

# ----- SMOKE TEST -----
python core/agent.py

# ----- RUN ONE TEST CLASS -----
pytest tests/test_agent.py::TestParseOutput -v

# ----- RUN ONE SPECIFIC TEST -----
pytest tests/test_agent.py::TestReasoningAgentUnit::test_invoke_parses_final_answer -v

# ----- VERBOSE WITH FULL OUTPUT -----
pytest tests/test_agent.py -v --tb=short
```

---

## 8. Next Build Steps

| Step | File | Paper section |
|---|---|---|
| **2 (next)** | `core/environment.py` | Tool execution (run_python, web_search, file_reader) |
| **3** | `core/memory.py` | Global state `S_t` builder + trajectory logger |
| **4** | `core/orchestrator.py` | Policy `pi` — the "puppeteer" that selects next agent |
| **5** | `training/reward.py` | `R(tau)` from §2.2 — terminal reward + efficiency penalty |
| **6** | `training/reinforce.py` | REINFORCE gradient update `theta <- theta + alpha * grad` |
| **7** | `agents/*.py` | All 9 concrete agents (planner, critic, reflector, etc.) |
| **8** | `extensions/*.py` | **3 original contributions** (step critic, agent registry, dissent) |
| **9** | `api/server.py` | FastAPI + WebSocket backend for dashboard |
| **10** | `dashboard/` | React + TypeScript live visualization |
| **11** | `benchmarks/evaluate.py` | GSM-Hard + MMLU-Pro evaluation runner |
