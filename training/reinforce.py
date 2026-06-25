"""
training/reinforce.py
=====================
The REINFORCE policy gradient training loop implemented via prompt optimization.
Instead of backpropagation through model weights, we update the policy in
context (in-context RL) by identifying high-reward and low-reward trajectories
and injecting them into the orchestrator prompt as few-shot exemplars.

Paper grounding:
- Equation 5: REINFORCE policy gradient:
    J(θ) = E_{π_θ}[R(τ)]
    ∇θJ(θ) ≈ (1/N) Σ_n Σ_t [∇θ log π_θ(a_t|S_t)] · R(τ)
- Parameter Update:
    θ ← θ + α · ∇θJ(θ)
- Section 2.2:
    "we employ REINFORCE as our underlying optimization framework.
     By doing so, the orchestration policy learns from previous
     executions, adaptively refining agent selection."
- Section 3 Implementation Details:
    "episode length to 4, parallel exploration up to 3, λ=0.1, γ=0.99"
"""

import os
import json
import time
import asyncio
import logging
import dataclasses
from typing import Optional, Any, Union

# ── sys.path guard for direct execution ──────────────────────────────────────
import sys as _sys
import os as _os
_project_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

# ── project imports ───────────────────────────────────────────────────────────
from core.orchestrator import Orchestrator
from core.agent import AgentFactory, ModelTier, ReasoningPattern
from core.environment import EnvironmentConfig
from training.reward import RewardFunction, RewardConfig, StepReward

# ── logging setup ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# 1.  TRAINING CONFIG
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class TrainingConfig:
    """
    Configuration for the REINFORCE training loop.

    Fields
    ------
    n_episodes : int
        Total training episodes.
    batch_size : int
        Batch size N for policy updates.
    learning_rate : float
        Learning rate α (step size) for prompt updates.
    checkpoint_every : int
        Interval (in episodes) to save policy state.
    eval_every : int
        Interval (in episodes) to run evaluation.
    max_examples : int
        Maximum number of few-shot examples (positive/negative) to keep in prompt.
    checkpoint_dir : str
        Directory to save PolicyState JSON checkpoints.
    log_dir : str
        Directory to save metrics logs.
    """
    n_episodes:        int   = 50
    batch_size:        int   = 3
    learning_rate:     float = 0.001
    checkpoint_every:  int   = 10
    eval_every:        int   = 10
    max_examples:      int   = 5
    checkpoint_dir:    str   = "checkpoints"
    log_dir:           str   = "logs"
    use_step_critic:   bool  = False

    @classmethod
    def from_dict(cls, d: dict) -> "TrainingConfig":
        """Build TrainingConfig from a plain dictionary."""
        valid_keys = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)


# ═════════════════════════════════════════════════════════════════════════════
# 2.  EPISODE METRICS
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class EpisodeMetrics:
    """
    Metrics logged for one completed episode.

    Fields
    ------
    episode : int
        The index of the episode.
    task : str
        The task text run during this episode.
    total_return : float
        R_0 — cumulative discounted reward starting at step 0.
    terminal_reward : float
        r ∈ [0, 1] — terminal correctness/quality reward.
    total_tokens : int
        Sum of all tokens consumed across the episode.
    total_steps : int
        Number of steps taken in the episode.
    elapsed_ms : float
        Episode execution duration in milliseconds.
    agent_sequence : list[str]
        Ordered sequence of selected agent names.
    status : str
        Final status of the environment (completed, truncated, failed, etc.).
    """
    episode:          int
    task:             str
    total_return:     float
    terminal_reward:  float
    total_tokens:     int
    total_steps:      int
    elapsed_ms:       float
    agent_sequence:   list[str]
    status:           str

    def to_dict(self) -> dict:
        """Serialize metrics to a dictionary."""
        return dataclasses.asdict(self)


# ═════════════════════════════════════════════════════════════════════════════
# 3.  POLICY STATE (FEW-SHOT MEMORY)
# ═════════════════════════════════════════════════════════════════════════════

@dataclasses.dataclass
class PolicyState:
    """
    Tracks the current learned policy in prompt space as few-shot exemplars.

    Fields
    ------
    positive_examples : list[dict]
        High-reward demonstrations.
    negative_examples : list[dict]
        Low-reward sequences to avoid.
    episode_count : int
        Number of episodes trained so far.
    best_return : float
        Highest reward return observed so far.
    avg_return_history : list[float]
        History of all total returns.
    """
    positive_examples:  list[dict] = dataclasses.field(default_factory=list)
    negative_examples:  list[dict] = dataclasses.field(default_factory=list)
    episode_count:      int        = 0
    best_return:        float      = -999.0
    avg_return_history: list[float] = dataclasses.field(default_factory=list)

    def add_episode(
        self,
        trajectory: list[dict],
        step_rewards: list[StepReward],
        terminal_reward: float,
        task: str = "",
        max_examples: int = 5,
    ) -> None:
        """
        Store episode in positive or negative few-shot logs if reward is notably good/bad.
        Uses the mean return of the last 10 episodes as a threshold.
        """
        if not step_rewards:
            return

        total_return = step_rewards[0].reward
        
        # Calculate mean of last 10 returns
        last_10 = self.avg_return_history[-10:]
        if last_10:
            mean_val = sum(last_10) / len(last_10)
        else:
            mean_val = 0.0

        # Update metrics
        self.avg_return_history.append(total_return)
        self.episode_count += 1
        if total_return > self.best_return:
            self.best_return = total_return

        # Prepare examplar summary
        agent_sequence = [sr.agent_name for sr in step_rewards]
        example = {
            "task": task,
            "sequence": " → ".join(agent_sequence),
            "return": total_return
        }

        # Classify as positive or negative
        if not last_10:
            # Cold start: classify purely on correctness
            if terminal_reward >= 1.0:
                self.positive_examples.append(example)
            else:
                self.negative_examples.append(example)
        else:
            if total_return > mean_val:
                self.positive_examples.append(example)
            elif total_return < mean_val:
                self.negative_examples.append(example)

        # Truncate lists to max_examples
        if len(self.positive_examples) > max_examples:
            self.positive_examples = self.positive_examples[-max_examples:]
        if len(self.negative_examples) > max_examples:
            self.negative_examples = self.negative_examples[-max_examples:]

    def build_few_shot_prompt(self) -> str:
        """
        Renders policy exemplars into a prompt-injectable few-shot string.
        """
        blocks = []

        if self.positive_examples:
            pos_lines = ["LEARNED EXAMPLES (from successful past episodes):"]
            for ex in self.positive_examples:
                pos_lines.append(f"Task type: {ex['task']}")
                pos_lines.append(f"Sequence that worked: {ex['sequence']}")
                pos_lines.append(f"Return: {ex['return']:.3f}")
                pos_lines.append("")
            blocks.append("\n".join(pos_lines).strip())

        if self.negative_examples:
            neg_lines = ["SEQUENCES TO AVOID:"]
            for ex in self.negative_examples:
                neg_lines.append(f"Task type: {ex['task']}")
                neg_lines.append(f"Sequence that failed: {ex['sequence']}")
                neg_lines.append(f"Return: {ex['return']:.3f}")
                neg_lines.append("")
            blocks.append("\n".join(neg_lines).strip())

        if not blocks:
            return ""

        return "\n\n".join(blocks)

    def save(self, path: str) -> None:
        """Save PolicyState to JSON."""
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        state_data = {
            "positive_examples": self.positive_examples,
            "negative_examples": self.negative_examples,
            "episode_count": self.episode_count,
            "best_return": self.best_return,
            "avg_return_history": self.avg_return_history,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "PolicyState":
        """Load PolicyState from JSON."""
        with open(path, "r", encoding="utf-8") as f:
            state_data = json.load(f)
        inst = cls()
        inst.positive_examples = state_data.get("positive_examples", [])
        inst.negative_examples = state_data.get("negative_examples", [])
        inst.episode_count = state_data.get("episode_count", 0)
        inst.best_return = state_data.get("best_return", -999.0)
        inst.avg_return_history = state_data.get("avg_return_history", [])
        return inst


# ═════════════════════════════════════════════════════════════════════════════
# 4.  TRAINER CLASS
# ═════════════════════════════════════════════════════════════════════════════

class Trainer:
    """
    Orchestrates the REINFORCE prompt-gradient optimization loop.
    """

    DEFAULT_TASKS = [
        "What is 15% of 200?",
        "A train travels 120km in 2 hours. What is its speed?",
        "If 5 workers complete a job in 8 days, how many days for 10 workers?",
        "What is the sum of angles in a triangle?",
        "A rectangle has length 12 and width 7. What is its area?",
        "If x + 5 = 13, what is x?",
        "What is 3/4 + 1/3? Simplify your answer.",
        "A store offers 20% off a $50 item. What is the final price?",
        "What is the square root of 196?",
        "If a car uses 6L per 100km, how much fuel for 250km?",
    ]

    def __init__(
        self,
        orchestrator: Orchestrator,
        reward_fn: RewardFunction,
        training_config: Optional[TrainingConfig] = None,
        task_dataset: Optional[list[str]] = None,
    ) -> None:
        """
        Set up the Trainer with environment configurations.
        """
        self.orchestrator = orchestrator
        self.reward_fn = reward_fn
        self.config = training_config or TrainingConfig()
        self.task_dataset = task_dataset or self.DEFAULT_TASKS

        # Initialise state
        self.policy_state = PolicyState()
        self.metrics_log: list[EpisodeMetrics] = []

        # Create directories
        os.makedirs(self.config.checkpoint_dir, exist_ok=True)
        os.makedirs(self.config.log_dir, exist_ok=True)

        # Initialize StepCritic if enabled
        if self.config.use_step_critic:
            from extensions.step_critic import StepCritic
            shared_client = None
            if self.orchestrator and self.orchestrator._policy_agent:
                shared_client = self.orchestrator._policy_agent.groq_client
            self.step_critic = StepCritic(groq_client=shared_client)
            self.reward_fn.config.use_step_critic = True
        else:
            self.step_critic = None

        # Store policy agent's baseline prompt for dynamic additions
        if self.orchestrator and self.orchestrator._policy_agent:
            self.base_system_prompt = self.orchestrator._policy_agent.config.role_prompt
        else:
            self.base_system_prompt = ""

        logger.info(
            "Trainer initialized with %d tasks | n_episodes=%d | batch_size=%d",
            len(self.task_dataset),
            self.config.n_episodes,
            self.config.batch_size,
        )

    async def run_training(self) -> list[EpisodeMetrics]:
        """
        Execute the REINFORCE training loop asynchronously.
        """
        for ep_idx in range(self.config.n_episodes):
            # 1. Sample task
            task = self.task_dataset[ep_idx % len(self.task_dataset)]

            logger.info("Episode %d/%d starting | task: %r", ep_idx + 1, self.config.n_episodes, task)

            # 2. Run episode
            result = await self.orchestrator.run_episode(task)

            # 3. Compute terminal_reward
            if result.success and result.final_answer.strip() != "":
                terminal_reward = 1.0
            elif result.status == "truncated":
                terminal_reward = 0.3
            else:
                terminal_reward = 0.0

            # 4. Compute step rewards
            step_scores = None
            if self.step_critic:
                scores = await self.step_critic.score_trajectory(task, result.trajectory)
                step_scores = [s.score for s in scores]

            step_rewards = self.reward_fn.compute_from_episode_result(
                result, terminal_reward, step_scores=step_scores
            )
            total_return = step_rewards[0].reward if step_rewards else 0.0

            # 5. Update policy state
            self.policy_state.add_episode(
                trajectory=result.trajectory,
                step_rewards=step_rewards,
                terminal_reward=terminal_reward,
                task=task,
                max_examples=self.config.max_examples,
            )

            # Build sequence list
            agent_sequence = [sr.agent_name for sr in step_rewards]

            # Store metrics
            metrics = EpisodeMetrics(
                episode=ep_idx + 1,
                task=task,
                total_return=total_return,
                terminal_reward=terminal_reward,
                total_tokens=result.total_tokens,
                total_steps=result.total_steps,
                elapsed_ms=result.elapsed_ms,
                agent_sequence=agent_sequence,
                status=result.status,
            )
            self.metrics_log.append(metrics)

            logger.info(
                "Episode %d complete | Return: %.3f | Status: %s | Sequence: %s",
                ep_idx + 1,
                total_return,
                result.status,
                " → ".join(agent_sequence),
            )

            # 6. Every batch_size episodes: update prompt gradient (few-shots)
            if (ep_idx + 1) % self.config.batch_size == 0:
                few_shot = self.policy_state.build_few_shot_prompt()
                self._inject_few_shot_prompt(few_shot)

                # Log batch summary
                batch_metrics = self.metrics_log[-self.config.batch_size:]
                avg_return = sum(m.total_return for m in batch_metrics) / len(batch_metrics)
                logger.info(
                    "Batch complete (episode %d) | Average Return (batch): %.3f",
                    ep_idx + 1,
                    avg_return,
                )

            # 7. Every checkpoint_every episodes: save checkpoints and logs
            if (ep_idx + 1) % self.config.checkpoint_every == 0:
                chk_path = os.path.join(
                    self.config.checkpoint_dir,
                    f"policy_ep{ep_idx + 1}.json",
                )
                self.policy_state.save(chk_path)
                self.save_metrics()
                logger.info("Saved checkpoint at: %s", chk_path)

            await asyncio.sleep(3)  # prevent Groq 429 rate limiting

        # Final checkpoint save at training conclusion
        self.save_metrics()
        if self.step_critic:
            await self.step_critic.close()
        logger.info("Training process completed.")
        return self.metrics_log

    def run_training_sync(self) -> list[EpisodeMetrics]:
        """
        Synchronous wrapper around run_training.
        """
        return asyncio.run(self.run_training())

    def _inject_few_shot_prompt(self, few_shot: str) -> None:
        """
        Update the orchestrator's policy agent system prompt.
        """
        if few_shot.strip():
            new_prompt = f"{self.base_system_prompt}\n\n{few_shot}"
        else:
            new_prompt = self.base_system_prompt
        self.orchestrator._policy_agent.config.role_prompt = new_prompt

    def save_metrics(self, path: Optional[str] = None) -> None:
        """
        Save metrics_log as JSON to log_dir/metrics.json.
        """
        target_path = path or os.path.join(self.config.log_dir, "metrics.json")
        parent = os.path.dirname(target_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        data = [m.to_dict() for m in self.metrics_log]
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def plot_training_curves(self) -> None:
        """
        Print ASCII training curves to the terminal.
        Plots Returns, Tokens, and Steps sequences using sparklines.
        """
        if not self.metrics_log:
            print("No metrics logged yet.")
            return

        returns = [m.total_return for m in self.metrics_log]
        tokens = [m.total_tokens for m in self.metrics_log]
        steps = [m.total_steps for m in self.metrics_log]

        def get_sparkline(data: list[Union[int, float]]) -> str:
            if not data:
                return ""
            min_v = float(min(data))
            max_v = float(max(data))
            v_range = max_v - min_v
            # Sparkline blocks
            blocks = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
            spark = []
            for v in data:
                if v_range == 0:
                    idx = 0
                else:
                    idx = int((float(v) - min_v) / v_range * (len(blocks) - 1))
                spark.append(blocks[idx])
            return "".join(spark)

        print("\n" + "=" * 60)
        print("  Puppeteer++ Training Curves")
        print("=" * 60)
        print(f"Episodes run: {len(self.metrics_log)}")
        print(f"Returns   : {get_sparkline(returns)}")
        print(f"            Min: {min(returns):.3f} | Max: {max(returns):.3f} | Avg: {sum(returns)/len(returns):.3f}")
        print(f"Tokens    : {get_sparkline(tokens)}")
        print(f"            Min: {min(tokens):d} | Max: {max(tokens):d} | Avg: {sum(tokens)/len(tokens):.1f}")
        print(f"Steps     : {get_sparkline(steps)}")
        print(f"            Min: {min(steps):d} | Max: {max(steps):d} | Avg: {sum(steps)/len(steps):.2f}")
        print("=" * 60 + "\n")


# ═════════════════════════════════════════════════════════════════════════════
# 5.  __main__ — SMOKE TEST
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    # Force stdout encoding to UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Set up basic logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\n" + "=" * 60)
    print("  Puppeteer++ -- training/reinforce.py smoke test")
    print("=" * 60 + "\n")

    groq_key_exists = bool(os.environ.get("GROQ_API_KEY"))

    if not groq_key_exists:
        # ── STRUCTURAL MOCK TEST (Offline) ────────────────────────────────────
        print("─── Running Offline Structural Tests ─────────────────────────")
        
        cfg = TrainingConfig(
            n_episodes=6,
            batch_size=2,
            checkpoint_every=3,
            checkpoint_dir="test_checkpoints",
            log_dir="test_logs"
        )
        
        # 1. Verify dataclass construction
        metrics = EpisodeMetrics(
            episode=1,
            task="Test Task",
            total_return=1.2,
            terminal_reward=1.0,
            total_tokens=150,
            total_steps=3,
            elapsed_ms=450.0,
            agent_sequence=["PlannerAgent", "ReasoningAgent", "ConcluderAgent"],
            status="completed"
        )
        assert metrics.to_dict()["episode"] == 1, "EpisodeMetrics dictionary serialization failure."
        assert cfg.use_step_critic is False, "Default use_step_critic should be False."
        assert TrainingConfig(use_step_critic=True).use_step_critic is True, "use_step_critic should be configurable."
        print("[OK] EpisodeMetrics and TrainingConfig constructed and verified.")

        # 2. Verify PolicyState construction and prompt rendering
        ps = PolicyState()
        mock_traj = [{"step": 0, "agent_name": "PlannerAgent", "agent_output": {"tokens_used": 100}}]
        mock_sr_good = [StepReward(step=0, agent_name="PlannerAgent", tokens_used=100, step_cost=0.0, step_score=0.0, reward=2.5)]
        mock_sr_bad = [StepReward(step=0, agent_name="PlannerAgent", tokens_used=100, step_cost=0.0, step_score=0.0, reward=-4.0)]

        # Simulate episodes addition
        ps.add_episode(mock_traj, mock_sr_good, 1.0, task="Test Task 1", max_examples=2)
        ps.add_episode(mock_traj, mock_sr_bad, 0.0, task="Test Task 2", max_examples=2)

        prompt = ps.build_few_shot_prompt()
        print("Generated Mock Prompt:")
        print("-" * 50)
        print(prompt)
        print("-" * 50)

        assert "LEARNED EXAMPLES" in prompt, "Missing Positive Exemplars heading."
        assert "SEQUENCES TO AVOID" in prompt, "Missing Negative Exemplars heading."
        print("[OK] PolicyState few-shot prompt formatting verified.")

        # Verify saves and loads
        ps.save("test_checkpoints/policy_test.json")
        loaded_ps = PolicyState.load("test_checkpoints/policy_test.json")
        assert loaded_ps.episode_count == 2, "PolicyState loading error."
        assert len(loaded_ps.positive_examples) == 1, "PolicyState positive list mismatch."
        assert len(loaded_ps.negative_examples) == 1, "PolicyState negative list mismatch."
        print("[OK] PolicyState serialization and deserialization verified.")

        # 3. Verify trainer and Sparkline curves
        trainer = Trainer(None, None, cfg) # type: ignore
        trainer.metrics_log = [
            EpisodeMetrics(1, "t1", 0.5, 1.0, 100, 2, 100.0, ["PlannerAgent", "ConcluderAgent"], "completed"),
            EpisodeMetrics(2, "t2", -1.0, 0.0, 200, 3, 200.0, ["PlannerAgent", "ReasoningAgent", "ConcluderAgent"], "failed"),
            EpisodeMetrics(3, "t3", 1.5, 1.0, 50, 1, 50.0, ["ConcluderAgent"], "completed"),
            EpisodeMetrics(4, "t4", 0.8, 1.0, 120, 2, 120.0, ["PlannerAgent", "ConcluderAgent"], "completed"),
            EpisodeMetrics(5, "t5", -2.0, 0.0, 300, 4, 300.0, ["PlannerAgent", "ReasoningAgent", "CriticAgent", "ConcluderAgent"], "truncated"),
            EpisodeMetrics(6, "t6", 2.0, 1.0, 80, 1, 80.0, ["ConcluderAgent"], "completed"),
        ]
        trainer.plot_training_curves()
        print("[OK] ASCII Sparkline curves display verified.")

        # Cleanup test outputs
        try:
            if os.path.exists("test_checkpoints/policy_test.json"):
                os.remove("test_checkpoints/policy_test.json")
            if os.path.exists("test_checkpoints"):
                os.rmdir("test_checkpoints")
            if os.path.exists("test_logs"):
                os.rmdir("test_logs")
        except Exception:
            pass

        print("\n[PARTIAL] No API key — structural tests passed")
        sys.exit(0)

    # ── LIVE RL TRAINING LOOP TEST (Requires Groq API Key) ────────────────────
    print("─── Running Live REINFORCE Prompt-RL Training ────────────────")
    try:
        # Build 5-agent pool
        pool = AgentFactory.create_pool([
            ReasoningPattern.PLANNER,
            ReasoningPattern.REASONING,
            ReasoningPattern.CRITIC,
            ReasoningPattern.CONCLUDER,
            ReasoningPattern.TERMINATOR,
        ])

        # Build EnvironmentConfig
        env_config = EnvironmentConfig(
            task="Live RL Task",
            max_steps=4
        )

        # Build Orchestrator
        orch = Orchestrator(
            agent_pool=pool,
            env_config=env_config
        )

        # Build RewardFunction
        reward_fn = RewardFunction()

        # Build TrainingConfig for Trainer
        # Short training block: 6 episodes, batch size 2, save every 3
        training_cfg = TrainingConfig(
            n_episodes=6,
            batch_size=2,
            checkpoint_every=3,
            checkpoint_dir="checkpoints",
            log_dir="logs",
            use_step_critic=True
        )

        # Instantiate Trainer
        trainer = Trainer(
            orchestrator=orch,
            reward_fn=reward_fn,
            training_config=training_cfg
        )

        # Execute training
        metrics_log = trainer.run_training_sync()

        # Logs summary prints
        n = len(metrics_log)
        returns = [m.total_return for m in metrics_log]
        best_return = max(returns)
        last_3_avg = sum(returns[-3:]) / 3.0
        total_tokens = sum(m.total_tokens for m in metrics_log)

        print("\n" + "=" * 50)
        print("  Training complete.")
        print("-" * 50)
        print(f"  Episodes run       : {n}")
        print(f"  Best return        : {best_return:.4f}")
        print(f"  Avg return (last 3): {last_3_avg:.4f}")
        print(f"  Total tokens used  : {total_tokens}")
        print("=" * 50 + "\n")

        # Plot ASCII curves
        trainer.plot_training_curves()

        # Assertions
        assert len(metrics_log) == 6, f"Expected 6 episodes logged, got {len(metrics_log)}"
        for m in metrics_log:
            assert m.status in ("completed", "terminated", "truncated", "failed"), f"Unknown status: {m.status}"
        
        # Check checkpoints and log files
        assert os.path.exists("checkpoints/policy_ep3.json"), "Checkpoint ep3 missing."
        assert os.path.exists("checkpoints/policy_ep6.json"), "Checkpoint ep6 missing."
        assert os.path.exists("logs/metrics.json"), "logs/metrics.json missing."

        print("[PASS] training/reinforce.py working")

    except Exception as e:
        print(f"\n✗ Live training smoke test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
