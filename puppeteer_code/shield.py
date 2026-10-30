"""
puppeteer_code/shield.py
========================
Action shielding: hard rules that remove actions which are pointless or
unsafe in the current state, before any policy (LLM, linear, heuristic)
chooses.  The policy learns *preferences*; the shield guarantees *sanity*:

  edits not yet tested        → only TestRunner (and Linter / Reviewer once)
  everything green + progress → Concluder (Linter / Reviewer once, if a diff exists)
  last step or budget spent   → Concluder (TestRunner first if edits are pending)
  Coder / Debugger            → need a scout first; Debugger needs a failed attempt
  Planner                     → at most once, before the first edit
  RepoScout / DocsSearch      → only when there is new information since last time
  Explorer                    → only after a stall (≥2 failed cycles or rollbacks)
  Terminator                  → only when stalled or near the step limit
"""

from __future__ import annotations


def allowed_actions(env, pool: list[str]) -> list[str]:
    st = env.state
    names = set(pool)
    step, max_steps = env.current_step, env.config.max_steps
    last = step >= max_steps - 1
    near_end = step >= max_steps - 3
    has_diff = bool(env.ws.changed_files())

    def keep(candidates) -> list[str]:
        out = [n for n in candidates if n in names]
        return out or (["ConcluderAgent"] if "ConcluderAgent" in names else sorted(names)[:1])

    if st.edits_since_test:
        if last:
            return keep(["TestRunnerAgent"])
        allowed = ["TestRunnerAgent"]
        if not st.linted_since_edit:
            allowed.append("LinterAgent")
        if not st.reviewed_since_edit:
            allowed.append("ReviewerAgent")
        return keep(allowed)

    if last or st.budget_exceeded:
        return keep(["ConcluderAgent"])

    green = st.last_tests.all_green
    has_targets = bool(st.target_tests)
    if green and (has_targets or st.improved):
        allowed = ["ConcluderAgent"]
        if has_diff and not st.linted_since_edit:
            allowed.append("LinterAgent")
        if has_diff and not st.reviewed_since_edit:
            allowed.append("ReviewerAgent")
        return keep(allowed)

    allowed: list[str] = []
    new_info = st.scouted_at_tests != st.tests_run
    if st.scouted == 0 or new_info:
        allowed.append("RepoScoutAgent")
    if not st.planned and st.edit_attempts == 0 and st.scouted > 0:
        allowed.append("PlannerAgent")
    if st.scouted > 0:
        allowed.append("CoderAgent")
        if st.last_tests.failing and st.edit_attempts >= 1:
            allowed.append("DebuggerAgent")
    if has_diff and not st.linted_since_edit:
        allowed.append("LinterAgent")
    if has_diff and not st.reviewed_since_edit:
        allowed.append("ReviewerAgent")
    if st.last_tests.failing and st.docs_at_tests != st.tests_run:
        allowed.append("DocsSearchAgent")
    if (st.tdd or not has_targets) and not near_end:
        allowed.append("TestWriterAgent")
    if st.stall >= 2 or len(st.rollbacks) >= 2:
        allowed.append("ExplorerAgent")
    if st.stall >= 3 or near_end:
        allowed.append("TerminatorAgent")
    if st.improved or near_end or st.stall >= 3:
        allowed.append("ConcluderAgent")
    return keep(allowed)
