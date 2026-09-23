"""
benchmarks/token_comparison.py
==============================
Token Efficiency Benchmark — Puppeteer++ vs Baseline Methods

Compares token usage across 5 fixed reasoning tasks using 5 different methods:
  1. Puppeteer++              — adaptive multi-agent orchestration (our system)
  2. Monolithic LLM           — single large model, one shot
  3. Fixed Multi-Agent        — all agents activated every step (no selection policy)
  4. Chain-of-Thought (CoT)   — single model with CoT prompting
  5. ReAct                    — Reason+Act pattern (simulated as sequential prompts)

Results are saved to benchmarks/results/token_comparison_results.json
and are consumed by benchmarks/dashboard.html for visualization.

Usage:
    python benchmarks/token_comparison.py
"""

import os
import sys
import json
import time
import math
import logging
import datetime

# ── SSL bypass ───────────────────────────────────────────────────────────────
import ssl
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    pass
try:
    _orig_ctx = ssl.create_default_context
    def _unverified(*a, **kw):
        ctx = _orig_ctx(*a, **kw)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ssl.create_default_context = _unverified
except Exception:
    pass

# ── UTF-8 stdout ──────────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── .env load ────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=None, override=False)
except ImportError:
    pass

# ── sys.path fix for direct execution ────────────────────────────────────────
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import httpx
from openai import OpenAI

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIG
# =============================================================================

GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
FAST_MODEL     = "openai/gpt-oss-20b"
QUALITY_MODEL  = "openai/gpt-oss-120b"
RESULTS_DIR    = os.path.join(os.path.dirname(__file__), "results")
RESULTS_FILE   = os.path.join(RESULTS_DIR, "token_comparison_results.json")

# Use a custom httpx client with SSL verification disabled to handle
# certificate issues on Windows dev machines (same approach as core/agent.py)
_http_client = httpx.Client(verify=False)
groq = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
    http_client=_http_client,
)

# Fixed benchmark tasks (same for all methods)
BENCHMARK_TASKS = [
    {
        "id": "T1",
        "name": "Math Reasoning",
        "category": "Arithmetic",
        "task": (
            "A train travels at 60 km/h. It needs to cover 210 km. "
            "There is a 30-minute stop midway. What is the total journey time? "
            "Show all working steps clearly."
        ),
        "expected_answer": "4 hours",
    },
    {
        "id": "T2",
        "name": "Logic Puzzle",
        "category": "Deductive Reasoning",
        "task": (
            "A bat and a ball together cost $1.10. The bat costs exactly $1.00 more "
            "than the ball. How much does the ball cost? Explain your reasoning and verify."
        ),
        "expected_answer": "$0.05",
    },
    {
        "id": "T3",
        "name": "Rate Problem",
        "category": "Proportional Reasoning",
        "task": (
            "If 5 machines make 5 widgets in 5 minutes, how long would it take "
            "100 machines to make 100 widgets? Show step-by-step reasoning and "
            "explain why the answer seems counterintuitive."
        ),
        "expected_answer": "5 minutes",
    },
    {
        "id": "T4",
        "name": "Code Analysis",
        "category": "Technical Reasoning",
        "task": (
            "A function doubles every element in a list and then filters out "
            "elements greater than 10. If the input is [1, 3, 5, 7, 9], "
            "what is the output? Show your reasoning step by step."
        ),
        "expected_answer": "[2, 6, 10]",
    },
    {
        "id": "T5",
        "name": "Multi-step Planning",
        "category": "Planning",
        "task": (
            "You have a 3-litre jug and a 5-litre jug. You need exactly 4 litres "
            "of water. Describe the minimum number of steps needed and list each step."
        ),
        "expected_answer": "6 steps",
    },
]

# =============================================================================
# HELPERS
# =============================================================================

def call_llm(messages, model, max_tokens=1024, temperature=0.3, max_retries=3):
    """Call Groq API with retries and return (content, tokens_used)."""
    for attempt in range(max_retries):
        try:
            resp = groq.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            tokens = resp.usage.total_tokens if resp.usage else 0
            content = resp.choices[0].message.content or ""
            return content, tokens
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                logger.warning("LLM call failed after %d attempts: %s", max_retries, e)
                return f"[Error: {e}]", 0


def separator(label):
    print(f"\n{'─' * 60}")
    print(f"  {label}")
    print('─' * 60)


# =============================================================================
# METHOD 1 — Puppeteer++ (Adaptive Multi-Agent Orchestration)
# =============================================================================

def run_puppeteer_plus(task):
    """Simulate Puppeteer++ adaptive orchestration."""
    agents = {
        "PlannerAgent": {
            "role": (
                "You are a concise task planner. Break the problem into 2-3 clear steps. "
                "End with: REASONING RESULT: [brief plan]\nFINAL ANSWER: [numbered steps]"
            ),
            "model": FAST_MODEL, "max_tokens": 400, "temperature": 0.3,
        },
        "ReasoningAgent": {
            "role": (
                "You are an expert reasoner. Execute the plan, solve the problem mathematically. "
                "Show your working. End with: REASONING RESULT: [working]\nFINAL ANSWER: [answer]"
            ),
            "model": FAST_MODEL, "max_tokens": 600, "temperature": 0.5,
        },
        "CriticAgent": {
            "role": (
                "You are a critical evaluator. Check the previous reasoning for errors. "
                "If correct, confirm briefly. End with: REASONING RESULT: [review]\nFINAL ANSWER: [verdict]"
            ),
            "model": FAST_MODEL, "max_tokens": 300, "temperature": 0.2,
        },
        "ConcluderAgent": {
            "role": (
                "You are a final answer synthesiser. Given prior reasoning, state the clean final answer. "
                "End with: REASONING RESULT: [summary]\nFINAL ANSWER: [final answer only]"
            ),
            "model": FAST_MODEL, "max_tokens": 250, "temperature": 0.1,
        },
    }

    selection_order = ["PlannerAgent", "ReasoningAgent", "CriticAgent", "ConcluderAgent"]
    memory = []
    total_tokens = 0
    steps_log = []

    for step, agent_name in enumerate(selection_order):
        agent = agents[agent_name]
        context_parts = []
        for prev_name, prev_content in memory[-3:]:
            snippet = prev_content[:200] if len(prev_content) > 200 else prev_content
            context_parts.append(f"[{prev_name}]: {snippet}")
        context_str = "\n".join(context_parts)

        messages = [
            {"role": "system", "content": agent["role"]},
            {"role": "user", "content": f"Task: {task}\n\nContext so far:\n{context_str}"},
        ]
        content, tokens = call_llm(messages, agent["model"], agent["max_tokens"], agent["temperature"])
        total_tokens += tokens
        memory.append((agent_name, content))
        steps_log.append({"step": step, "agent": agent_name, "tokens": tokens})
        print(f"    Step {step+1}: {agent_name:18s} | {tokens:4d} tokens")

    final_content = memory[-1][1] if memory else ""
    final_answer = final_content.split("FINAL ANSWER:")[-1].strip() if "FINAL ANSWER:" in final_content else ""

    return {
        "method": "Puppeteer++",
        "total_tokens": total_tokens,
        "steps": len(steps_log),
        "steps_log": steps_log,
        "final_answer": final_answer,
        "success": total_tokens > 0,
    }


# =============================================================================
# METHOD 2 — Monolithic LLM
# =============================================================================

def run_monolithic_llm(task):
    """Baseline: single large model, no orchestration."""
    messages = [
        {"role": "system", "content": (
            "You are a highly capable reasoning assistant. Solve the given problem "
            "step by step, show all working, verify your answer, and state the final answer clearly."
        )},
        {"role": "user", "content": task},
    ]
    content, tokens = call_llm(messages, QUALITY_MODEL, max_tokens=2048, temperature=0.3)
    steps_log = [{"step": 0, "agent": "MonolithicLLM", "tokens": tokens}]
    print(f"    Step 1: MonolithicLLM         | {tokens:4d} tokens")
    return {
        "method": "Monolithic LLM",
        "total_tokens": tokens,
        "steps": 1,
        "steps_log": steps_log,
        "final_answer": content[:200],
        "success": tokens > 0,
    }


# =============================================================================
# METHOD 3 — Fixed Multi-Agent (no policy, all agents run)
# =============================================================================

def run_fixed_multi_agent(task):
    """Baseline: ALL agents activate every step (no selection policy)."""
    agents = [
        {"name": "PlannerAgent",   "role": "You are a task planner. Break the problem into sub-steps.",           "model": FAST_MODEL, "max_tokens": 500, "temperature": 0.3},
        {"name": "ReasoningAgent", "role": "You are a reasoner. Solve the problem step by step.",                  "model": FAST_MODEL, "max_tokens": 700, "temperature": 0.5},
        {"name": "CriticAgent",    "role": "You critically evaluate reasoning for errors.",                        "model": FAST_MODEL, "max_tokens": 400, "temperature": 0.2},
        {"name": "ValidatorAgent", "role": "You validate and double-check the reasoning and answer.",              "model": FAST_MODEL, "max_tokens": 400, "temperature": 0.2},
        {"name": "ConcluderAgent", "role": "You synthesise all previous reasoning into a final clean answer.",    "model": FAST_MODEL, "max_tokens": 300, "temperature": 0.1},
    ]

    total_tokens = 0
    steps_log = []
    memory_parts = []

    for idx, agent in enumerate(agents):
        context_str = "\n".join(memory_parts[-3:]) if memory_parts else "No prior context."
        messages = [
            {"role": "system", "content": agent["role"]},
            {"role": "user", "content": f"Task: {task}\n\nPrior outputs:\n{context_str}"},
        ]
        content, tokens = call_llm(messages, agent["model"], agent["max_tokens"], agent["temperature"])
        total_tokens += tokens
        memory_parts.append(f"[{agent['name']}]: {content[:200]}")
        steps_log.append({"step": idx, "agent": agent["name"], "tokens": tokens})
        print(f"    Step {idx+1}: {agent['name']:18s} | {tokens:4d} tokens")
        time.sleep(0.5)

    return {
        "method": "Fixed Multi-Agent",
        "total_tokens": total_tokens,
        "steps": len(steps_log),
        "steps_log": steps_log,
        "final_answer": memory_parts[-1][:200] if memory_parts else "",
        "success": total_tokens > 0,
    }


# =============================================================================
# METHOD 4 — Chain-of-Thought
# =============================================================================

def run_chain_of_thought(task):
    """Baseline: CoT prompting with single model."""
    cot_user = (
        f"{task}\n\n"
        "Let's think step by step:\n"
        "Step 1: Understand the problem.\n"
        "Step 2: Identify what we know.\n"
        "Step 3: Solve each part.\n"
        "Step 4: Verify the answer.\n"
        "Final Answer: [state the answer clearly]"
    )
    messages = [
        {"role": "system", "content": (
            "You are a reasoning assistant. Think through this problem step by step. "
            "Use the chain-of-thought approach: break down, reason through each part, "
            "check your work, then give the final answer."
        )},
        {"role": "user", "content": cot_user},
    ]
    content, tokens = call_llm(messages, QUALITY_MODEL, max_tokens=1500, temperature=0.3)
    steps_log = [{"step": 0, "agent": "CoT-LLM", "tokens": tokens}]
    print(f"    Step 1: CoT-LLM               | {tokens:4d} tokens")
    return {
        "method": "Chain-of-Thought",
        "total_tokens": tokens,
        "steps": 1,
        "steps_log": steps_log,
        "final_answer": content[:200],
        "success": tokens > 0,
    }


# =============================================================================
# METHOD 5 — ReAct
# =============================================================================

def run_react(task):
    """Baseline: ReAct (Reason+Act) pattern — 3 sequential calls."""
    total_tokens = 0
    steps_log = []
    history = []

    # Phase 1: Thought
    messages = [
        {"role": "system", "content": "You are reasoning with the ReAct approach. First, THINK about the problem."},
        {"role": "user",   "content": f"Task: {task}\n\nThought: What do I know and what do I need to find?"},
    ]
    thought, tokens = call_llm(messages, FAST_MODEL, max_tokens=500, temperature=0.4)
    total_tokens += tokens
    history.append(f"THOUGHT: {thought[:200]}")
    steps_log.append({"step": 0, "agent": "ReAct-Thought", "tokens": tokens})
    print(f"    Step 1: ReAct-Thought         | {tokens:4d} tokens")
    time.sleep(0.5)

    # Phase 2: Act
    messages = [
        {"role": "system", "content": "You are in the ACT phase of ReAct. Execute reasoning based on your thought."},
        {"role": "user",   "content": f"Task: {task}\n\nPrevious thought:\n{history[-1]}\n\nAct: Compute or reason through the answer now."},
    ]
    action, tokens = call_llm(messages, FAST_MODEL, max_tokens=600, temperature=0.5)
    total_tokens += tokens
    history.append(f"ACTION: {action[:200]}")
    steps_log.append({"step": 1, "agent": "ReAct-Act", "tokens": tokens})
    print(f"    Step 2: ReAct-Act             | {tokens:4d} tokens")
    time.sleep(0.5)

    # Phase 3: Observe
    messages = [
        {"role": "system", "content": "You are in the OBSERVE phase of ReAct. Observe the results and state the final answer."},
        {"role": "user",   "content": (
            f"Task: {task}\n\nHistory:\n" + "\n".join(history) +
            "\n\nObservation: Based on the action above, state the final definitive answer."
        )},
    ]
    observation, tokens = call_llm(messages, FAST_MODEL, max_tokens=400, temperature=0.2)
    total_tokens += tokens
    steps_log.append({"step": 2, "agent": "ReAct-Observe", "tokens": tokens})
    print(f"    Step 3: ReAct-Observe         | {tokens:4d} tokens")

    return {
        "method": "ReAct",
        "total_tokens": total_tokens,
        "steps": len(steps_log),
        "steps_log": steps_log,
        "final_answer": observation[:200],
        "success": total_tokens > 0,
    }


# =============================================================================
# BENCHMARK RUNNER
# =============================================================================

METHODS = [
    ("Puppeteer++",       run_puppeteer_plus),
    ("Monolithic LLM",   run_monolithic_llm),
    ("Fixed Multi-Agent", run_fixed_multi_agent),
    ("Chain-of-Thought",  run_chain_of_thought),
    ("ReAct",             run_react),
]


def run_benchmark():
    print("\n" + "=" * 60)
    print("  Puppeteer++ Token Efficiency Benchmark")
    print("  Comparing 5 methods x 5 tasks = 25 runs")
    print("=" * 60)

    if not GROQ_API_KEY:
        print("\n[ERROR] GROQ_API_KEY not set. Please add it to your .env file.")
        sys.exit(1)

    results = {
        "benchmark_run": datetime.datetime.now().isoformat(),
        "model_fast": FAST_MODEL,
        "model_quality": QUALITY_MODEL,
        "tasks": [],
        "methods_summary": {},
    }

    all_task_results = []

    for task_def in BENCHMARK_TASKS:
        task_id   = task_def["id"]
        task_name = task_def["name"]
        task_text = task_def["task"]

        separator(f"Task {task_id}: {task_name}")
        print(f"  \"{task_text[:80]}...\"")

        task_result = {
            "id":       task_id,
            "name":     task_name,
            "category": task_def["category"],
            "task":     task_text,
            "expected": task_def["expected_answer"],
            "methods":  {},
        }

        for method_name, method_fn in METHODS:
            print(f"\n  [{method_name}]")
            try:
                t0 = time.monotonic()
                result = method_fn(task_text)
                elapsed_ms = (time.monotonic() - t0) * 1000
                result["elapsed_ms"] = round(elapsed_ms, 1)
                print(f"    TOTAL: {result['total_tokens']:5d} tokens | {elapsed_ms:.0f}ms")
                task_result["methods"][method_name] = result
                time.sleep(2.0)   # rate-limit pause between methods
            except Exception as e:
                print(f"    [ERROR] {e}")
                task_result["methods"][method_name] = {
                    "method": method_name, "total_tokens": 0,
                    "steps": 0, "steps_log": [], "error": str(e), "success": False,
                }

        all_task_results.append(task_result)
        time.sleep(3.0)   # pause between tasks

    results["tasks"] = all_task_results

    # Compute per-method summaries
    method_totals = {m[0]: [] for m in METHODS}
    for task_result in all_task_results:
        for method_name, m_result in task_result["methods"].items():
            method_totals[method_name].append(m_result.get("total_tokens", 0))

    puppeteer_avg = sum(method_totals["Puppeteer++"]) / max(len(method_totals["Puppeteer++"]), 1)

    for method_name, token_list in method_totals.items():
        avg   = sum(token_list) / max(len(token_list), 1)
        total = sum(token_list)
        extra = avg - puppeteer_avg
        pct   = ((avg - puppeteer_avg) / max(avg, 1)) * 100 if avg > 0 else 0.0

        results["methods_summary"][method_name] = {
            "avg_tokens_per_task":       round(avg, 1),
            "total_tokens_all_tasks":    total,
            "per_task_tokens":           token_list,
            "extra_tokens_vs_puppeteer": round(extra, 1),
            "savings_pct_vs_puppeteer":  round(pct, 1),
        }

    # Print summary
    separator("Summary: Token Usage by Method")
    print(f"\n  {'Method':<25} {'Avg Tokens':>12} {'vs Puppeteer++':>16} {'Savings %':>10}")
    print(f"  {'─'*25} {'─'*12} {'─'*16} {'─'*10}")
    for method_name, summary in results["methods_summary"].items():
        avg   = summary["avg_tokens_per_task"]
        extra = summary["extra_tokens_vs_puppeteer"]
        pct   = summary["savings_pct_vs_puppeteer"]
        mark  = " <- OUR SYSTEM" if method_name == "Puppeteer++" else ""
        extra_str = f"+{extra:.0f}" if extra > 0 else f"{extra:.0f}"
        print(f"  {method_name:<25} {avg:>12.0f} {extra_str:>16} {pct:>9.1f}%{mark}")

    # Save results
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n  Results saved to: {RESULTS_FILE}")
    print("\n" + "=" * 60)
    print("  BENCHMARK COMPLETE")
    print("  Open benchmarks/dashboard.html to view the analytics.")
    print("=" * 60 + "\n")

    return results


if __name__ == "__main__":
    run_benchmark()
