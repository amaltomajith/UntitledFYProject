# Puppeteer++ and CompactCode: benchmarks, methods and results

A self-contained briefing. Hand this file to a person or a model as context. Every number in a generated
block comes from a run file under `runs/` (gitignored, on the owner's machine) through
`benchmarks/reasoning/report.py`, never typed by hand. Last updated 2026-11-05.

**Status of the reasoning run:** complete, 224 tasks, one seed, finished 2026-11-05 (the first line of the reasoning
block below says so). Rescored from the stored replies with the final checkers: 0 of 896 rows changed.

---

## 1. What is being measured

**Puppeteer++** is a B.Tech (IT) final-year thesis at Christ University, Bengaluru . It extends the
NeurIPS 2025 paper *Multi-Agent Collaboration via Evolving Orchestration*: a central "puppeteer" policy picks which
agent acts next, and is meant to be trained with REINFORCE to be both correct and cheap.

There are two systems, and they are benchmarked separately:

| System | Code | What it does | Benchmarks |
|---|---|---|---|
| Thesis core, the general reasoning engine | `core/` (`core.Orchestrator`), `training/` | An LLM policy picks one of 5 agents per step: Planner, Reasoning, Critic, Concluder, Terminator. Episode length 4. | GSM8K, GSM-Hard, MMLU-Pro, BBH (§3) |
| CompactCode, the coding application (Python name Puppeteer-Code) | `puppeteer_code/` | A coding assistant. A heuristic or learned policy routes roles to a cheap or strong model; tests decide what is kept. | PCodeBench-Build (§4), PCodeBench-Mini and -Repo (§5) |

The question every benchmark asks is the thesis question: **does orchestration buy correctness, and at what token
cost**, against simpler baselines on the same model?

### Rules every result follows

- Real providers only (Nebius Token Factory, Groq). Simulated (`sim`), `oracle` and `mock` runs test mechanisms and
  are never reported.
- Provider errors are excluded from aggregates and their count is reported.
- Each table says how many tasks and seeds it covers. All runs so far are **one seed**.
- No method gets a kinder answer parser than another.

---

## 2. Summary of findings

1. **Reasoning (thesis core), 224 tasks, one seed:** the puppeteer answered 78.1% correctly, against 60.3% for
   one step-by-step call and 29.5% for one answer-only call (both p < 0.001). It does **not** beat the fixed
   four-agent pipeline: 78.1% against 76.8%, 12 tasks only it got right against 9 only the pipeline got right,
   McNemar p 0.66. It used about 52% more tokens than the pipeline (5,026 against 3,301 per task). The gain is from
   giving the model several steps to reason, not from the policy's choices. The policy is untrained (see §3.5).
2. **Build from a prompt (application):** the puppeteer **did not beat** a plain tool loop. It used 2 to 3 times the
   tokens and at best matched its pass rate. Its one lead is zero false success claims, but every puppeteer failure
   ended at the token limit, so this does not show its checks caught them.
3. **Fix a bug (application):** every method fixed every bug measured, so success does not separate the methods.
   These runs compare cost only. On the multi-file repo the puppeteer used fewer tokens and much smaller prompts than
   one-shot prompting. On single-file bugs one-shot was cheaper.
4. **Not run:** GAIA and ToolBench (reasons in §3.6).

---

## 3. Reasoning benchmark for the thesis core

### 3.1 Datasets and subsets

Fixed-seed samples (seed 0), frozen in `benchmarks/reasoning/tasks/*.jsonl` and committed, so a rerun uses exactly
the same questions. `python benchmarks/reasoning/datasets.py --check` proves that every task's checker accepts its
gold answer and rejects a wrong one (224 tasks plus 8 smoke tasks, 0 problems).

| Dataset | Origin | What it tests | Source split | Subset |
|---|---|---|---|---|
| GSM8K | Cobbe et al., OpenAI, 2021 | Grade-school maths word problems, 2 to 8 steps | `openai/gsm8k` main/test (1,319) | 50, uniform |
| GSM-Hard | Gao et al., 2023 (PAL) | GSM8K with numbers replaced by large ones | `reasoning-machines/gsm-hard` (1,319) | 50, uniform |
| MMLU-Pro | Wang et al., TIGER-Lab, 2024 | 14 subjects, 10 options (A to J) | `TIGER-Lab/MMLU-Pro` test (12,032) | 70: 5 per subject |
| BIG-Bench Hard | Suzgun et al., 2022 | 27 symbolic and algorithmic subtasks (the paper counts 23 tasks; some have variants) | `lukaemon/bbh` test | 54: 2 per subtask |

8 more tasks (2 per dataset, outside the subsets) are in `smoke.jsonl` and were used only to check the pipeline.

### 3.2 Methods

All four methods use **one model** for every call and the **same output cap per call**, so the only difference is
orchestration.

| Method | Calls | What it is |
|---|---|---|
| `direct` | 1 | Answer only, no working shown |
| `cot` | 1 | Step-by-step reasoning, then the answer |
| `fixed` | 4 | Planner, Reasoning, Critic, Concluder, in that order, using the core's own agent classes and memory |
| `puppeteer` | up to 8 | `core.Orchestrator` unchanged: the LLM policy picks each agent (one policy call plus one agent call per step), with the engine's rule that the last step is the Concluder |

### 3.3 Protocol

- Provider Nebius Token Factory, model `nvidia/Nemotron-3_5-Lightning` ($0.06 in, $0.24 out per 1M tokens).
- **Thinking off** for every call of every method (`chat_template_kwargs.enable_thinking=false`). With thinking on,
  the model's hidden reasoning used up the engine's small output caps: the policy reply became a paragraph of
  reasoning instead of an agent name, and the engine fell back to its first agent. Turning thinking off for
  everyone is the fair fix; a thinking-on run is future work.
- Output cap 4,096 tokens per call (policy 1,024). The core's own caps (128 to 1,024) are raised equally for all.
- Temperature: baselines 0.7 (the Reasoning agent's); core agents keep their own (0.2 to 0.7).
- Prompts: each question ends with a format hint that constrains only the `FINAL ANSWER:` line. The one-call
  methods repeat their instruction at the end of the user turn, as the core agents do with their action prompt. In
  the smoke check this model ignored a CoT instruction placed only in the system turn (3-token replies).
- **Every call is counted**, the policy's included. `core.Orchestrator` itself never adds the policy's tokens to
  `EpisodeResult.total_tokens`; `benchmarks/reasoning/client.py` counts them for this benchmark.
- **Scoring** (`benchmarks/reasoning/checkers.py`): the text after the last `FINAL ANSWER:`. GSM numbers: integer
  golds exact, non-integer golds within 0.01. MMLU-Pro: the option letter. BBH: normalised exact match, `(B)` equal to
  `B`. A reply with no tag falls back to the last number, a bare letter, or an "answer is X" phrase, the same for
  every method. For the multi-agent methods the scored text is the raw reply of the step the engine's own
  `get_final_answer()` picks.
- Statistics: accuracy with a Wilson 95% interval; exact McNemar test on tasks both methods answered.
- Order: every method on a task before the next task, one process, saved after every row, `--resume` after a stop.

### 3.4 Results

<!-- reasoning:start -->
Source: `runs/reasoning-nebius-lightning.json` · provider `nebius` · model `nvidia/Nemotron-3_5-Lightning` · thinking `off` · max_steps 4 · output cap 4096 per call (policy 1024) · seed [0] · 224 tasks · 896 scored rows · 0 provider-error rows excluded · complete · total 2,056,883 tokens, $0.2529

**All four**

| Method | Correct | Accuracy | 95% CI (Wilson) | Tokens/task | Prompt/task | Completion/task | Calls/task | $/task | Tokens per correct |
|---|---|---|---|---|---|---|---|---|---|
| direct (1 call, answer only) | 66/224 | 29.5% | 23.9% to 35.7% | 204 | 196 | 8 | 1.0 | $0.000014 | 692 |
| cot (1 call, step by step) | 135/224 | 60.3% | 53.7% to 66.5% | 652 | 204 | 447 | 1.0 | $0.000120 | 1,081 |
| fixed (Planner, Reasoning, Critic, Concluder) | 172/224 | 76.8% | 70.8% to 81.8% | 3,301 | 2,064 | 1,237 | 4.0 | $0.000421 | 4,299 |
| puppeteer (core.Orchestrator) | 175/224 | 78.1% | 72.3% to 83.0% | 5,026 | 3,507 | 1,519 | 7.0 | $0.000575 | 6,433 |

| Puppeteer vs | Shared tasks | Only puppeteer right | Only other right | Exact McNemar p |
|---|---|---|---|---|
| direct | 224 | 117 | 8 | < 0.001 |
| cot | 224 | 47 | 7 | < 0.001 |
| fixed | 224 | 12 | 9 | 0.664 |

**GSM8K**

| Method | Correct | Accuracy | 95% CI (Wilson) | Tokens/task | Prompt/task | Completion/task | Calls/task | $/task | Tokens per correct |
|---|---|---|---|---|---|---|---|---|---|
| direct (1 call, answer only) | 7/50 | 14.0% | 7.0% to 26.2% | 143 | 139 | 4 | 1.0 | $0.000009 | 1,019 |
| cot (1 call, step by step) | 28/50 | 56.0% | 42.3% to 68.8% | 247 | 147 | 100 | 1.0 | $0.000033 | 440 |
| fixed (Planner, Reasoning, Critic, Concluder) | 46/50 | 92.0% | 81.2% to 96.8% | 2,497 | 1,632 | 866 | 4.0 | $0.000306 | 2,714 |
| puppeteer (core.Orchestrator) | 47/50 | 94.0% | 83.8% to 97.9% | 3,922 | 3,061 | 860 | 7.1 | $0.000390 | 4,172 |

| Puppeteer vs | Shared tasks | Only puppeteer right | Only other right | Exact McNemar p |
|---|---|---|---|---|
| direct | 50 | 40 | 0 | < 0.001 |
| cot | 50 | 20 | 1 | < 0.001 |
| fixed | 50 | 1 | 0 | 1.000 |

**GSM-Hard**

| Method | Correct | Accuracy | 95% CI (Wilson) | Tokens/task | Prompt/task | Completion/task | Calls/task | $/task | Tokens per correct |
|---|---|---|---|---|---|---|---|---|---|
| direct (1 call, answer only) | 5/50 | 10.0% | 4.3% to 21.4% | 148 | 141 | 7 | 1.0 | $0.000010 | 1,478 |
| cot (1 call, step by step) | 23/50 | 46.0% | 33.0% to 59.6% | 328 | 149 | 178 | 1.0 | $0.000052 | 712 |
| fixed (Planner, Reasoning, Critic, Concluder) | 32/50 | 64.0% | 50.1% to 75.9% | 2,699 | 1,705 | 994 | 4.0 | $0.000341 | 4,217 |
| puppeteer (core.Orchestrator) | 36/50 | 72.0% | 58.3% to 82.5% | 4,584 | 3,175 | 1,409 | 7.2 | $0.000529 | 6,366 |

| Puppeteer vs | Shared tasks | Only puppeteer right | Only other right | Exact McNemar p |
|---|---|---|---|---|
| direct | 50 | 31 | 0 | < 0.001 |
| cot | 50 | 14 | 1 | < 0.001 |
| fixed | 50 | 5 | 1 | 0.219 |

**MMLU-Pro**

| Method | Correct | Accuracy | 95% CI (Wilson) | Tokens/task | Prompt/task | Completion/task | Calls/task | $/task | Tokens per correct |
|---|---|---|---|---|---|---|---|---|---|
| direct (1 call, answer only) | 32/70 | 45.7% | 34.6% to 57.3% | 281 | 272 | 9 | 1.0 | $0.000019 | 615 |
| cot (1 call, step by step) | 46/70 | 65.7% | 54.0% to 75.8% | 1,210 | 280 | 930 | 1.0 | $0.000240 | 1,841 |
| fixed (Planner, Reasoning, Critic, Concluder) | 54/70 | 77.1% | 66.0% to 85.4% | 4,452 | 2,649 | 1,802 | 4.0 | $0.000592 | 5,771 |
| puppeteer (core.Orchestrator) | 52/70 | 74.3% | 63.0% to 83.1% | 6,370 | 4,076 | 2,294 | 6.8 | $0.000795 | 8,574 |

| Puppeteer vs | Shared tasks | Only puppeteer right | Only other right | Exact McNemar p |
|---|---|---|---|---|
| direct | 70 | 23 | 3 | < 0.001 |
| cot | 70 | 7 | 1 | 0.070 |
| fixed | 70 | 2 | 4 | 0.688 |

**BBH**

| Method | Correct | Accuracy | 95% CI (Wilson) | Tokens/task | Prompt/task | Completion/task | Calls/task | $/task | Tokens per correct |
|---|---|---|---|---|---|---|---|---|---|
| direct (1 call, answer only) | 22/54 | 40.7% | 28.7% to 54.0% | 212 | 202 | 11 | 1.0 | $0.000015 | 521 |
| cot (1 call, step by step) | 38/54 | 70.4% | 57.2% to 80.9% | 603 | 210 | 393 | 1.0 | $0.000107 | 857 |
| fixed (Planner, Reasoning, Critic, Concluder) | 40/54 | 74.1% | 61.1% to 83.9% | 3,112 | 2,038 | 1,074 | 4.0 | $0.000380 | 4,201 |
| puppeteer (core.Orchestrator) | 40/54 | 74.1% | 61.1% to 83.9% | 4,716 | 3,488 | 1,228 | 6.9 | $0.000504 | 6,366 |

| Puppeteer vs | Shared tasks | Only puppeteer right | Only other right | Exact McNemar p |
|---|---|---|---|---|
| direct | 54 | 23 | 5 | < 0.001 |
| cot | 54 | 6 | 4 | 0.754 |
| fixed | 54 | 4 | 4 | 1.000 |

**What the puppeteer's policy did**

- Episodes: 224; mean agent steps 3.49; statuses {'completed': 224}.
- First agent chosen: {'ReasoningAgent': 188, 'PlannerAgent': 36}.
- Agent activations overall: {'ReasoningAgent': 430, 'ConcluderAgent': 224, 'PlannerAgent': 76, 'CriticAgent': 51}.
- Policy replies that named no known agent (engine fell back to the first agent): 0.

<!-- reasoning:end -->

### 3.5 How to read these results

- **Single calls are weak here because thinking is off.** With no room to reason, `direct` mostly guesses on maths.
  This is a property of the setup, and it is why the comparison that matters for the thesis is **puppeteer against
  fixed**: same agents, same budget per call, the policy's choices the only difference.
- **Puppeteer against fixed** is the test of orchestration, and it shows **no significant difference**: p 0.66 over
  all 224 tasks, and p above 0.2 on every dataset. The puppeteer is ahead on GSM-Hard (72% against 64%, p 0.22) and
  behind on MMLU-Pro (74.3% against 77.1%, p 0.69); neither is significant.
- **Tokens:** the puppeteer spends about 52% more than the fixed pipeline, because each step adds a policy call and
  the agents' context grows with every step (`Memory.get_context_for_agent` resends all prior steps). Per correct
  answer: 6,433 tokens against 4,299.
- **The policy is untrained.** It is the LLM policy with the engine's default prompt. The core's "REINFORCE"
  (`training/reinforce.py`) is prompt-space improvement, not gradient training, and was not run for this benchmark.
- **What the policy did:** it opened with the Reasoning agent in 188 of 224 episodes, used the Critic only 51 times,
  never chose the Terminator, and never named an unknown agent. Episodes averaged 3.5 agent steps, against the fixed
  pipeline's 4, so the policy mostly reasoned repeatedly rather than critiquing.
- **The partial figures quoted before the run finished were higher** (about 91% and 87% on the first 306 rows),
  because those rows were all of GSM8K, the easiest set here for the multi-agent methods, and the first 27 GSM-Hard
  tasks. Only the numbers above are final.

### 3.6 Not run, and why

| Benchmark | Why it was not run |
|---|---|
| GAIA (Mialon et al., 2023) | Gated on Hugging Face: it needs a token with its terms accepted (none on this machine). Its questions need web search, file reading and code execution, and the core agents have no tools. Running it tool-less would measure nothing useful. The harness supports it (`datasets.py` builds 20 Level-1 questions with no attached file when `HF_TOKEN` is set) and never commits GAIA questions, per its terms. |
| ToolBench (Qin et al., 2023) | Needs RapidAPI keys, its own tool server, and a GPT-4 judge (ToolEval). The core has no tool-calling agents. |

### 3.7 Threats to validity

- One seed, and subsets of 50 to 70 questions: the intervals are wide. Small differences are noise.
- GSM8K and MMLU questions may be in the model's training data.
- Some GSM-Hard gold answers are known to be wrong (the dataset swapped numbers into questions without checking
  that the answer still makes sense, for example negative counts). All methods face the same labels.
- One model. A larger model, or thinking on, may change the ranking.
- The subsets were drawn once and not used for tuning. The harness was debugged on the separate smoke tasks only.

### 3.8 Reproduce

```bash
python benchmarks/reasoning/datasets.py --check          # verify the frozen tasks (no network)
python benchmarks/reasoning/datasets.py --build          # refetch (overwrites tasks/; HF_TOKEN adds GAIA)
python -m benchmarks.reasoning.run --smoke --out runs/reasoning-smoke.json --cache runs/cache/reasoning
python -m benchmarks.reasoning.run --datasets gsm8k,gsm-hard,mmlu-pro,bbh \
    --methods direct,cot,fixed,puppeteer --cache runs/cache/reasoning \
    --out runs/reasoning-nebius-lightning.json --resume
python -m benchmarks.reasoning.run --rescore --out runs/reasoning-nebius-lightning.json   # after a checker fix
python -m benchmarks.reasoning.report --write docs/BENCHMARKS.md --reasoning runs/reasoning-nebius-lightning.json \
    --build runs/buildbench-nebius.json runs/buildbench-nebius-150k.json \
    --fix runs/bench-groq-test.json runs/bench-groq-repo.json runs/bench-groq-repo-20b.json
pytest tests/test_reasoning_bench.py -q                   # offline
```

Options: `--model`, `--thinking off|on|default`, `--max-tokens`, `--limit N`, `--seed`.

---

## 4. PCodeBench-Build: build software from a prompt (application)

9 tasks (6 Python, 3 Node), from a small module up to a three-module package, starting from an empty folder. The
score comes from **hidden acceptance tests** the model never sees, run on a copy of its output.
`python benchmarks/build_tasks/build.py --check` proves each task fair: a reference solution passes and an empty
folder fails. The quota fallback is off, so each row used the models it names. `false_claims` counts runs that
reported success but failed the hidden tests.

Methods: `heuristic` is the CompactCode assistant with its puppeteer policy (Planner, TestWriter, Builder, Debugger,
Reviewer...); `direct` is one Builder tool loop.

Nebius Token Factory, 2026-10-30; FAST `nvidia/Nemotron-3_5-Lightning`, QUALITY `nvidia/nemotron-3-super-120b-a12b`;
one seed; 0 rows excluded. 60,000 tokens is the primary limit; 150,000 is a sensitivity run.

<!-- build:start -->
| Token limit | Method | Tasks passed | Hidden tests | Tokens/task | $/task | False claims | Source |
|---|---|---|---|---|---|---|---|
| 60,000 | heuristic | 6/9 | 81.8% | 51,575 | $0.018811 | 0 | `runs/buildbench-nebius.json` |
| 60,000 | direct | 7/9 | 89.8% | 25,573 | $0.009166 | 2 | `runs/buildbench-nebius.json` |
| 150,000 | heuristic | 7/9 | 89.8% | 81,801 | $0.031366 | 0 | `runs/buildbench-nebius-150k.json` |
| 150,000 | direct | 7/9 | 89.8% | 27,642 | $0.009887 | 2 | `runs/buildbench-nebius-150k.json` |
<!-- build:end -->

- **The puppeteer did not beat the plain loop.** At 60k it passed one task fewer with twice the tokens; at 150k it
  matched the pass rate at about three times the tokens.
- Its zero false claims should not be oversold: every puppeteer failure ended at the token limit.
- The tasks were used to debug the engine, so this is a development set, not a test set.
- In a sample of 152 calls, about 83% of tokens were prompts resent on each tool turn: the first cost to reduce.

---

## 5. PCodeBench-Mini and -Repo: fix a bug (application, Fix mode)

- **Mini:** 37 single-module bug-fix tasks in 7 modules, train/test split by module, hidden holdout tests.
- **Repo:** 44 tasks in one shared repository, including 7 cross-module bugs whose only visible failure is in another
  module's tests.
- The integrity guard blocks test edits by non-test agents, falling test counts, added skips and removed asserts.

Methods: `heuristic` (the puppeteer, Fix mode); `fixed` (every agent on every bug); `single-shot` (one call with all
source files); `react` (all files plus a growing transcript); `react-tools` (file list and read/grep tools plus a
growing transcript). Groq free tier, `openai/gpt-oss-120b` (and a `gpt-oss-20b` setting), seed 0.

<!-- fix:start -->
| Run | Dataset | Model | Tasks measured | Method | Success | Tokens/task | Largest prompt (chars) | Provider errors excluded |
|---|---|---|---|---|---|---|---|---|
| `bench-groq-test` | PCodeBench-Mini test | gpt-oss-120b | 10 of 10 | heuristic | 10/10 | 1,886 | 2,899 | 0 |
| `bench-groq-test` | PCodeBench-Mini test | gpt-oss-120b | 10 of 10 | fixed | 10/10 | 9,879 | 3,522 | 0 |
| `bench-groq-test` | PCodeBench-Mini test | gpt-oss-120b | 10 of 10 | single-shot | 10/10 | 1,024 | 2,185 | 0 |
| `bench-groq-test` | PCodeBench-Mini test | gpt-oss-120b | 10 of 10 | react | 10/10 | 1,837 | 2,995 | 0 |
| `bench-groq-repo` | PCodeBench-Repo test | gpt-oss-120b | 7 of 17 | heuristic | 7/7 | 2,391 | 3,284 | 6 |
| `bench-groq-repo` | PCodeBench-Repo test | gpt-oss-120b | 2 of 17 | react-tools | 2/2 | 9,179 | 6,621 | 6 |
| `bench-groq-repo` | PCodeBench-Repo test | gpt-oss-120b | 6 of 17 | single-shot | 6/6 | 3,077 | 10,313 | 6 |
| `bench-groq-repo` | PCodeBench-Repo test | gpt-oss-120b | 6 of 17 | react | 6/6 | 3,173 | 10,541 | 6 |
| `bench-groq-repo-20b` | PCodeBench-Repo test | gpt-oss-20b | 2 of 17 | heuristic | 2/2 | 4,438 | 3,344 | 0 |
| `bench-groq-repo-20b` | PCodeBench-Repo test | gpt-oss-20b | 1 of 17 | single-shot | 1/1 | 2,918 | 10,293 | 0 |
| `bench-groq-repo-20b` | PCodeBench-Repo test | gpt-oss-20b | 1 of 17 | react | 1/1 | 2,981 | 10,521 | 0 |
<!-- fix:end -->

- Every method succeeded on every task measured, so these numbers compare cost, not skill.
- The Repo runs stopped at Groq's daily token cap (7 of 17 tasks, and 2 of 17 for the 20B setting). Methods stopped at
  different points, so compare them only on shared tasks: on the 6 tasks both finished, heuristic used about 2,503
  tokens against 3,077 for single-shot (19% fewer), with prompts about a third the size.
- On single-file Mini bugs a one-shot prompt was cheaper (about 1,024 tokens against 1,886).

### Policy training (Fix mode)

`LinearPolicy` trained by true REINFORCE on the PCodeBench-Repo train split on Groq, warm-started from weights trained
on the simulator: 36 episodes so far, all succeeded, about 3,000 tokens each. Far too few to show learning, and it has
not been compared with the heuristic policy. The assistant's own `LinearAssistantPolicy` has no real weights yet.

---

## 6. Where things are

| Path | What |
|---|---|
| `benchmarks/reasoning/` | The reasoning harness: `datasets.py`, `checkers.py`, `client.py`, `methods.py`, `run.py`, `stats.py`, `report.py`, frozen `tasks/` |
| `tests/test_reasoning_bench.py` | Offline tests for it |
| `benchmarks/build_tasks/`, `puppeteer_code/assistant/buildbench.py` | PCodeBench-Build |
| `benchmarks/code_tasks/`, `puppeteer_code/bench.py`, `baselines.py` | PCodeBench-Mini and -Repo |
| `runs/*.json` | Raw results (gitignored, on the owner's machine) |
| `web/public/data/*.json` | What the site shows, written by `python -m puppeteer_code export-web` (refuses sim, oracle, mock, smoke) |
| https://compactcode.vercel.app/benchmarks | The public page |
| `benchmarks/token_comparison.py`, `run_live_proof.py`, `results/` | **Legacy.** They bypass `core.Orchestrator` and never check answers. Never quote them. |

## 7. Open items

1. More seeds (3 or more), then a thinking-on run and a larger model (`nvidia/nemotron-3-super-120b-a12b`).
2. Train the core policy for real and compare trained against untrained on the same subsets.
3. Bound the agents' context in `core/memory.py`, the main token cost of the puppeteer here.
4. Tools for the core agents, which GAIA would need.
5. Fresh PCodeBench-Build tasks with 3 seeds, so Build becomes a real test set.
