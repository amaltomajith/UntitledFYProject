"""
training/reward.py
==================
The reward function R(τ) for the REINFORCE training loop.
It evaluates a completed episode trajectory and computes cumulative rewards
at each step (t = 0 ... T) to compute learning signals for the policy gradient.

Paper grounding:
- Equation 6:
    R_t = r − λ · C_T            if t == T  (terminal step)
    R_t = γ · R_{t+1} − λ · C_t   if t < T   (intermediate steps)
- Section 2.2:
    "we penalize excessive computational expenditure...
     incentivizes the orchestrator to achieve correct and
     high-quality solutions while minimizing unnecessary computation"
- Cost calculation:
    C_t = F · log(1 + t/φ)
    where F is the token count at step t, φ is the max episode length (max_steps),
    and t is the step index (0-based).

Our extension — step-level reward (Extension 1):
- We add a β · s_t term to the intermediate reward:
    R_t = γ · R_{t+1} − λ · C_t + β · s_t   (t < T)
    where s_t ∈ [0,1] is the step critic score (0.0 if not enabled or unavailable).
"""

import dataclasses
import math
import logging
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.orchestrator import EpisodeResult

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# 1.  REWARD CONFIG
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class RewardConfig:
    """
    Hyperparameters for cumulative and step-wise reward calculation.

    Fields
    ------
    lambda_efficiency : float
        Weight (λ) penalizing computational step costs (excessive token usage).
        Default: 0.1.
    gamma : float
        Discount factor (γ) for propagates returns backward.
        Default: 0.99.
    beta_step_critic : float
        Weight (β) for dense intermediate feedback from the step critic.
        Default: 0.3.
    use_step_critic : bool
        Whether to enable dense intermediate step rewards (Extension 1).
        Default: False (backward compatible with the paper baseline).
    max_steps : int
        Max step count (φ) for the episode, matching EnvironmentConfig.max_steps.
        Default: 4.
    """
    lambda_efficiency: float = 0.1
    gamma:             float = 0.99
    beta_step_critic:  float = 0.3
    use_step_critic:   bool  = False
    max_steps:         int   = 4

    @classmethod
    def from_dict(cls, d: dict) -> "RewardConfig":
        """
        Build a RewardConfig from a plain dictionary.
        Safely ignores extra keys to prevent issues.
        """
        valid_keys = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)


# ═════════════════════════════════════════════════════════════════════════════
# 2.  STEP REWARD DATA CLASS
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class StepReward:
    """
    Reward metrics computed at a single step of the trajectory.

    Fields
    ------
    step : int
        The 0-based step index.
    agent_name : str
        The name of the agent activated at this step.
    tokens_used : int
        The number of tokens consumed (F) by this agent invocation.
    step_cost : float
        The efficiency penalty computed cost C_t = F * log(1 + t/phi).
    step_score : float
        The dense step score s_t (0.0 if not enabled or not evaluated).
    reward : float
        The cumulative return R_t propagating backward from this step.
    """
    step:          int
    agent_name:    str
    tokens_used:   int
    step_cost:     float
    step_score:    float
    reward:        float

    def to_dict(self) -> dict:
        """Serialize StepReward to a dictionary."""
        return dataclasses.asdict(self)


# ═════════════════════════════════════════════════════════════════════════════
# 3.  REWARD FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

class RewardFunction:
    """
    Computes cumulative returns R_t across an episode trajectory.
    Uses backward induction from step T down to step 0.
    """

    def __init__(self, config: Optional[RewardConfig] = None) -> None:
        """
        Initialize the RewardFunction with a config or use default.
        """
        self.config = config or RewardConfig()

    def compute(
        self,
        trajectory: list[dict],
        terminal_reward: float,
        step_scores: Optional[list[float]] = None,
    ) -> list[StepReward]:
        """
        Compute R_t rewards backwards from step T to 0.

        Parameters
        ----------
        trajectory : list[dict]
            The list of step records from environment.get_full_trajectory().
            Each dict must contain: step, agent_name, agent_output (with tokens_used).
        terminal_reward : float
            r ∈ [0, 1] — terminal correctness/quality reward of the final answer.
        step_scores : list[float], optional
            The step critic scores s_t for intermediate steps.
            If None or use_step_critic is False, all s_t defaults to 0.0.

        Returns
        -------
        list[StepReward]
            List of step rewards sorted in forward order (step 0 first).
        """
        N = len(trajectory)
        if N == 0:
            return []

        # Extract tokens used per step and calculate step costs C_t = F_t * log(1 + t / phi)
        phi = self.config.max_steps if self.config.max_steps > 0 else 1
        step_costs = []
        step_tokens = []
        agent_names = []

        for i, step_dict in enumerate(trajectory):
            agent_names.append(step_dict.get("agent_name", "UnknownAgent"))
            # Safe extraction of tokens_used
            output = step_dict.get("agent_output", {})
            tokens = 0
            if isinstance(output, dict):
                tokens = int(output.get("tokens_used", 0))
            elif hasattr(output, "tokens_used"):
                tokens = int(output.tokens_used)

            step_tokens.append(tokens)

            # C_t = F_t * log(1 + t / phi)
            t = step_dict.get("step", i)
            cost = float(tokens) * math.log(1.0 + float(t) / float(phi))
            step_costs.append(cost)

        # Extract step critic scores s_t (0.0 if not enabled or index out of range)
        scores = [0.0] * N
        if self.config.use_step_critic and step_scores is not None:
            for i in range(N):
                if i < len(step_scores):
                    scores[i] = float(step_scores[i])

        # Backward accumulation
        rewards = [0.0] * N
        T = N - 1

        # R_T = r - lambda * C_T
        rewards[T] = float(terminal_reward) - (self.config.lambda_efficiency * step_costs[T])

        # R_t = gamma * R_{t+1} - lambda * C_t + beta * s_t  (for t < T)
        for t in range(T - 1, -1, -1):
            s_t = scores[t]
            rewards[t] = (
                (self.config.gamma * rewards[t + 1])
                - (self.config.lambda_efficiency * step_costs[t])
                + (self.config.beta_step_critic * s_t)
            )

        # Build list of StepReward objects in forward order
        step_rewards = []
        for i in range(N):
            sr = StepReward(
                step=trajectory[i].get("step", i),
                agent_name=agent_names[i],
                tokens_used=step_tokens[i],
                step_cost=step_costs[i],
                step_score=scores[i],
                reward=rewards[i],
            )
            step_rewards.append(sr)

        return step_rewards

    def compute_from_episode_result(
        self,
        episode_result: Any,
        terminal_reward: float,
        step_scores: Optional[list[float]] = None,
    ) -> list[StepReward]:
        """
        Convenience wrapper that accepts EpisodeResult directly (as object or dict).
        """
        if isinstance(episode_result, dict):
            trajectory = episode_result.get("trajectory", [])
        else:
            trajectory = getattr(episode_result, "trajectory", [])
        return self.compute(trajectory, terminal_reward, step_scores)

    def get_total_return(self, step_rewards: list[StepReward]) -> float:
        """
        Returns R_0 — the total return for the episode.
        Used as the policy gradient return objective in REINFORCE.
        """
        if not step_rewards:
            return 0.0
        return step_rewards[0].reward

    def get_efficiency_score(self, step_rewards: list[StepReward]) -> float:
        """
        Returns performance/cost ratio for dashboard/metric logs.
        efficiency = terminal_reward / total_step_cost
        Reconstructs terminal_reward from the terminal step.
        """
        if not step_rewards:
            return 0.0

        total_cost = sum(sr.step_cost for sr in step_rewards)
        if total_cost == 0.0:
            return 0.0

        # Reconstruct r: R_T = r - lambda * C_T => r = R_T + lambda * C_T
        terminal_step = step_rewards[-1]
        r = terminal_step.reward + (self.config.lambda_efficiency * terminal_step.step_cost)
        return r / total_cost

    def summarise(self, step_rewards: list[StepReward]) -> dict:
        """
        Generates a summary dictionary of rewards and costs.
        Useful for logging, checkpoints, and visualization dashboards.
        """
        if not step_rewards:
            return {
                "total_return":     0.0,
                "terminal_reward":  0.0,
                "total_step_cost":  0.0,
                "efficiency_score": 0.0,
                "steps":            0,
                "per_step":         [],
            }

        total_return = self.get_total_return(step_rewards)
        total_cost = sum(sr.step_cost for sr in step_rewards)

        # Reconstruct r
        terminal_step = step_rewards[-1]
        r = terminal_step.reward + (self.config.lambda_efficiency * terminal_step.step_cost)
        efficiency_score = self.get_efficiency_score(step_rewards)

        return {
            "total_return":     float(total_return),
            "terminal_reward":  float(r),
            "total_step_cost":  float(total_cost),
            "efficiency_score": float(efficiency_score),
            "steps":            len(step_rewards),
            "per_step":         [sr.to_dict() for sr in step_rewards],
        }


# ═════════════════════════════════════════════════════════════════════════════
# 4.  __main__ — SMOKE TEST
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    # Force stdout encoding to UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("\n" + "=" * 60)
    print("  Puppeteer++ -- training/reward.py smoke test")
    print("=" * 60 + "\n")

    # Construct mock trajectory matching the orchestrator smoke test outputs
    mock_trajectory = [
        {
            "step": 0,
            "agent_name": "PlannerAgent",
            "agent_output": {"tokens_used": 222}
        },
        {
            "step": 1,
            "agent_name": "ReasoningAgent",
            "agent_output": {"tokens_used": 418}
        },
        {
            "step": 2,
            "agent_name": "CriticAgent",
            "agent_output": {"tokens_used": 431}
        },
        {
            "step": 3,
            "agent_name": "ConcluderAgent",
            "agent_output": {"tokens_used": 496}
        }
    ]

    print("Trajectory length: 4 steps.")
    print("Tokens consumed: Planner=222, Reasoning=418, Critic=431, Concluder=496.\n")

    # ── Test 1 — Paper baseline (no step critic) ─────────────────────────────
    print("─── Test 1: Paper Baseline (no step critic) ──────────────────")
    config = RewardConfig(
        lambda_efficiency=0.1,
        gamma=0.99,
        beta_step_critic=0.3,
        use_step_critic=False,
        max_steps=4
    )
    reward_fn = RewardFunction(config)
    step_rewards = reward_fn.compute(
        trajectory=mock_trajectory,
        terminal_reward=1.0,  # Correct answer
    )

    print("Per-Step Reward Details:")
    for sr in step_rewards:
        print(f"  Step {sr.step:2d} | Agent: {sr.agent_name:15s} | Tokens: {sr.tokens_used:3d} | "
              f"Cost C_t: {sr.step_cost:6.2f} | R_t: {sr.reward:6.3f}")

    summary = reward_fn.summarise(step_rewards)
    print("\nSummary Dictionary:")
    for k, v in summary.items():
        if k != "per_step":
            print(f"  {k:18s}: {v}")
    print()

    # Assertions
    assert len(step_rewards) == 4, f"Expected 4 step rewards, got {len(step_rewards)}"
    assert step_rewards[-1].reward < 1.0, f"Terminal reward should be reduced by cost: {step_rewards[-1].reward}"
    assert step_rewards[0].reward < step_rewards[-1].reward, (
        f"Cumulative R_0 ({step_rewards[0].reward}) should be smaller than R_T ({step_rewards[-1].reward}) "
        f"due to discount and efficiency cost accumulation."
    )
    print("[OK] Test 1 passed.\n")

    # ── Test 2 — With step critic (Extension 1) ─────────────────────────────
    print("─── Test 2: Step Critic (Extension 1) ───────────────────────")
    config_ext = RewardConfig(
        lambda_efficiency=0.1,
        gamma=0.99,
        beta_step_critic=0.3,
        use_step_critic=True,
        max_steps=4
    )
    reward_fn_ext = RewardFunction(config_ext)
    step_scores = [0.3, 0.7, 0.8, 0.0]  # Planner low, Reasoning good, Critic great, Concluder final

    step_rewards_ext = reward_fn_ext.compute(
        trajectory=mock_trajectory,
        terminal_reward=1.0,
        step_scores=step_scores,
    )

    print(f"{'Step':5s} | {'Baseline R_t':14s} | {'Extension R_t':14s} | {'Diff':8s}")
    print("-" * 50)
    for b_sr, e_sr in zip(step_rewards, step_rewards_ext):
        diff = e_sr.reward - b_sr.reward
        print(f"{b_sr.step:5d} | {b_sr.reward:14.5f} | {e_sr.reward:14.5f} | {diff:+8.5f}")
    print()

    # Assertions
    for i in range(3):
        assert step_rewards_ext[i].reward > step_rewards[i].reward, (
            f"Step {i} extension reward ({step_rewards_ext[i].reward}) "
            f"should be strictly greater than baseline ({step_rewards[i].reward}) since s_t > 0."
        )
    assert abs(step_rewards_ext[-1].reward - step_rewards[-1].reward) < 1e-9, (
        f"Terminal step rewards should be identical as s_T = 0 or step critic is not applied to T. "
        f"Baseline: {step_rewards[-1].reward}, Extension: {step_rewards_ext[-1].reward}"
    )
    print("[OK] Test 2 passed.\n")

    # ── Test 3 — Failed episode ──────────────────────────────────────────────
    print("─── Test 3: Failed Episode ──────────────────────────────────")
    step_rewards_fail = reward_fn.compute(
        trajectory=mock_trajectory,
        terminal_reward=0.0,  # Incorrect/Failed answer
    )
    for sr in step_rewards_fail:
        print(f"  Step {sr.step:2d} | Agent: {sr.agent_name:15s} | R_t: {sr.reward:6.3f}")

    assert step_rewards_fail[-1].reward < 0, f"Failed terminal reward should be negative, got {step_rewards_fail[-1].reward}"
    print("\n[OK] Failed episode produces negative terminal reward.")
    print("[OK] Test 3 passed.\n")

    print("=" * 60)
    print("[PASS] training/reward.py working")
    print("=" * 60 + "\n")
