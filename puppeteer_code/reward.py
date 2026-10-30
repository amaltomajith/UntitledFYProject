"""
puppeteer_code/reward.py
========================
Objective, test-based rewards for coding episodes, plugged into the
Puppeteer++ reward recursion (training/reward.py):

    R_T = r − λ·C_T
    R_t = γ·R_{t+1} − λ·C_t + β·s_t

Terminal reward r (from the final, independent test run):
    r = pass_rate + 0.5·success − 0.5·[regressions] − 1.0·[integrity violation]
        + 0.1·[lint clean]

Step credit s_t (replaces the LLM StepCritic for coding):
    the signed, clipped test-pass delta produced by an edit is credited to
    the step of the agent that WROTE the code, not to the TestRunner that
    measured it.  Rejected edits, syntax errors and rollbacks are negative.

Cost C_t = (tokens_t / token_budget) · log(1 + t/φ), so token cost is on the
same scale as correctness instead of swamping it (audit finding #4).
"""

from __future__ import annotations

from typing import Optional

from training.reward import RewardConfig, RewardFunction, StepReward


def default_reward_config(max_steps: int) -> RewardConfig:
    return RewardConfig(lambda_efficiency=0.1, gamma=0.95, beta_step_critic=0.5,
                        use_step_critic=True, max_steps=max_steps, token_budget=4000)


def terminal_reward(final: dict) -> float:
    r = float(final["final_tests"]["pass_rate"])
    if final.get("success"):
        r += 0.5
    if final.get("regressions"):
        r -= 0.5
    if final.get("integrity_violations"):
        r -= 1.0
    if final.get("lint_clean"):
        r += 0.1
    return r


def episode_rewards(env, final: dict, config: Optional[RewardConfig] = None) -> tuple[list[StepReward], dict]:
    config = config or default_reward_config(env.config.max_steps)
    traj = env.get_full_trajectory()
    scores = [env.state.credit.get(i, 0.0) for i in range(len(traj))]
    fn = RewardFunction(config)
    r = terminal_reward(final)
    steps = fn.compute(traj, r, scores)
    summary = fn.summarise(steps)
    summary["terminal_reward"] = r
    return steps, summary
