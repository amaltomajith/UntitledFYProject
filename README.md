# Puppeteer++
### Adaptive Multi-Agent Orchestration with Evolving Topology
> Based on "Multi-Agent Collaboration via Evolving Orchestration"
> (NeurIPS 2025) — extended with five original contributions.

## What This Is
Puppeteer++ is a production-grade multi-agent orchestration framework built entirely from scratch in Python to coordinate heterogeneous language model agents. The system abstracts agents as `(m, r, t)` triples (model, reasoning pattern, tools) and uses a learned selector policy ($\pi$) to activate agents sequentially, allowing the team topology to evolve step-by-step to match the task at hand. By combining dynamic topology evolution with reinforcement learning, Puppeteer++ optimizes both task success rates and token efficiency.

## Research Contributions
Puppeteer++ extends the original NeurIPS 2025 framework with five key research contributions that address critical limitations identified in the original paper:

1. **Step-Level Reward Critic (Extension 1 - Gap 1)**: Instantiates a lightweight step-level LLM critic providing dense feedback ($s_t \in [0, 1]$) for intermediate steps, resolving the original paper's limitation of relying solely on coarse, final-output-only rewards.
2. **Dynamic Agent Injection (Extension 2 - Gap 2)**: Integrates an LLM-based `TaskClassifier` that analyzes runtime needs and injects utility agents (e.g., `PythonAgent`, `WebSearchAgent`) mid-episode, overcoming the limitation of a fixed pool and bridging Roshan's NeuralForge domain sector modules (Engineering, HR, Finance).
3. **Dissent-Driven Consensus & Over-Reliance Detector (Extension 3 - Gap 3)**: Implements an analytical consensus and soft majority vote checker to detect over-reliance on individual agents, preventing reasoning collapse and task hijacking.
4. **Offline Policy Gradient Training (REINFORCE)**: Implements a full policy optimization loop using the REINFORCE algorithm to fine-tune the puppeteer's selector policy ($\pi_\theta$) based on both task success and stepwise efficiency costs ($C_t$).
5. **Fast Groq Client Wrapper with Connection Pooling**: Integrates a centralized, OpenAI-compatible Groq API client with global rate-limit throttling and exponential back-off to handle free-tier API rate limits during high-throughput training episodes.

## Architecture
```
┌─────────────────────────────────────────────────────────────┐
│                NeuralForge Application Layer                │
│       (Domain Sector Modules: Engineering, HR, Finance)      │
└──────────────────────────────┬──────────────────────────────┘
                               │ (calls set_agent_pool())
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                     Puppeteer++ Engine                      │
│                                                             │
│  ┌─────────────────┐ ┌─────────────────┐ ┌───────────────┐  │
│  │     core/       │ │   extensions/   │ │   training/   │  │
│  │ (Agent, Memory, │ │ (Step Critic,   │ │ (Reward,      │  │
│  │  Environment,   │ │  Registry,      │ │  REINFORCE)   │  │
│  │  Orchestrator)  │ │  Dissent)       │ │               │  │
│  └─────────────────┘ └─────────────────┘ └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Project Structure
```
puppeteer-plus/
├── core/                    # Core framework engine and abstractions
│   ├── __init__.py          # Makes core/ a Python package
│   ├── agent.py             # BaseAgent class, Groq API client wrapper, and standard agents
│   ├── environment.py       # Manages execution state, trajectory transitions, and stopping criteria
│   ├── memory.py            # Global state S_t manager, window summaries, and activation tracking
│   └── orchestrator.py      # Selects next agents at each step using the selection policy pi
├── extensions/              # Original research contributions extending NeurIPS 2025 paper
│   ├── step_critic.py       # Dense intermediate reward feedback (Extension 1)
│   ├── agent_registry.py    # Dynamic agent injection, TaskClassifier, and domain router (Extension 2)
│   └── dissent.py           # Dissent-driven over-reliance detector (Extension 3) # PLANNED
├── training/                # Reinforcement learning and optimizer modules
│   ├── reward.py            # Terminal reward formulation and efficiency penalties
│   └── reinforce.py         # REINFORCE training loop implementation for selection policy optimization
├── tests/                   # Automated pytest suite
│   ├── __init__.py          # Makes tests/ as a Python package
│   ├── conftest.py          # Pytest CLI configuration for live API tests
│   └── test_agent.py        # Comprehensive offline unit tests and live integration tests for agents
├── configs/                 # Hyperparameter configuration files
│   └── default.yaml         # Central YAML configuration file # PLANNED
├── tools/                   # Executable system tools for agents # PLANNED
│   ├── python_interpreter.py# Safe code execution environment # PLANNED
│   └── web_search.py        # Web search interface for search-enabled agents # PLANNED
├── api/                     # Backend API server
│   └── server.py            # FastAPI + WebSocket server for real-time visualization # PLANNED
├── dashboard/               # Frontend user interface
│   └── index.html           # React + TypeScript live dashboard # PLANNED
├── benchmarks/              # Evaluation benchmarks for Puppeteer++
│   └── evaluate.py          # Benchmark runner for GSM-Hard and MMLU-Pro datasets # PLANNED
├── .env                     # Local environment file for API keys (gitignored)
├── .env.example             # Sample environment template file
├── .gitignore               # Excludes virtual environments and sensitive credentials from version control
├── requirements.txt         # Project dependencies list
├── BUILD.md                 # Detailed testing and validation verification guide
├── SETUP.md                 # Virtual environment and OS-specific setup instructions
└── README.md                # Project overview and documentation (this file)
```

## Quick Start
### Prerequisites
* Python 3.11+
* Groq API key (free at [console.groq.com](https://console.groq.com))
* Optional: Ollama for local inference ([ollama.com](https://ollama.com))

### Installation
```bash
# Clone the repository
git clone https://github.com/amaltomajith/puppeteer-plus.git
cd puppeteer-plus

# Install requirements
pip install -r requirements.txt

# Setup environmental variables
cp .env.example .env
# Edit .env and insert your GROQ_API_KEY
```

### Run the engine
```bash
# Run a single episode orchestrator (runs structural smoke test)
python core/orchestrator.py

# Run training loop (simulates 6 episodes of REINFORCE policy optimization)
python training/reinforce.py

# Run step-level critic module (Extension 1 structural verification)
python extensions/step_critic.py

# Run agent registry module (Extension 2 dynamic classifier verification)
python extensions/agent_registry.py
```

## Configuration
Hyperparameters are managed via `configs/default.yaml` to govern the agent pools, training schedules, and limits. Below is a mapping of key configurations:

| Parameter | Default Value | Description | Paper Reference |
| :--- | :--- | :--- | :--- |
| `max_steps` | `4` | Maximum number of steps/activations per episode. | Appendix B.1, Table 4 |
| `max_tokens_per_step` | `1024` | Budget limit on tokens generated per agent step. | Section 2.2 Cost $C_t$ |
| `max_summary_chars` | `500` | Summary limit for historical memory to fit LLM window. | Section 2.1 State $S_t$ |
| `max_context_steps` | `10` | Rolling context window of steps visible to Orchestrator. | Section 2.1 state $\Phi$ |
| `include_reasoning` | `true` | Appends both reasoning rationales and final outputs to Memory. | Section 2.1 |
| `selection_temperature` | `0.2` | Randomness factor when policy LLM selects agents. | Section 3 Setup |
| `check_every_n_steps` | `2` | Update cadence for TaskClassifier agent injection. | Section D Gaps |
| `max_pool_size` | `9` | Maximum capability limit on active pool size. | Section D Gaps |
| `min_pool_size` | `3` | Floor threshold protecting agents from pruning. | Section D Gaps |
| `enable_pruning` | `true` | Allows pruning inactive utility agents after step 4. | Section D Gaps |
| `beta_step_critic` | `0.3` | Weight factor ($\beta$) for step-wise dense rewards. | Section 2.2 Reward |
| `use_step_critic` | `false` | Enables StepCritic trajectory feedback during training. | Section 2.2 / Section D |

## Build Status
| Component / File | Status | Notes |
| :--- | :--- | :--- |
| `core/agent.py` | ✅ Complete | Triple $(m,r,t)$ and standard factory |
| `core/environment.py` | ✅ Complete | Trajectory transition and status management |
| `core/memory.py` | ✅ Complete | Bounded history summary and activation counts |
| `core/orchestrator.py` | ✅ Complete | Selection loop and policy coordinator |
| `training/reward.py` | ✅ Complete | Stepwise reward function with cost penalty |
| `training/reinforce.py` | ✅ Complete | REINFORCE reinforcement learning trainer |
| `extensions/step_critic.py` | ✅ Complete | Intermediate dense evaluator (Extension 1) |
| `extensions/agent_registry.py`| ✅ Complete | Dynamic injection registry (Extension 2) |
| `extensions/dissent.py` | ⏳ Planned | Consensus checking module (Extension 3) |
| `configs/default.yaml` | ⏳ Planned | YAML parameter configurations |
| `tools/*` | ⏳ Planned | File reader, web search, python interpreter |
| `api/server.py` | ⏳ Planned | WebSockets and FastAPI backend server |
| `dashboard/*` | ⏳ Planned | Monitoring frontend React dashboard |
| `benchmarks/evaluate.py` | ⏳ Planned | MMLU-Pro and GSM-Hard benchmark evaluator |

## Tech Stack
| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Application Layer** | NeuralForge Sector Modules | Business task orchestration (Engineering, Finance, HR) |
| **Orchestration Layer** | Puppeteer++ (Python Core) | Multi-agent coordinate loop and selection policy |
| **Inference API** | Groq Cloud SDK (Llama 3) | High-speed LLM executions for fast iteration |
| **Optimization Layer** | Policy Gradient (REINFORCE) | Reinforcement learning optimization of policy selection |
| **Verification & Test**| Pytest | Automated regression checking and integration tests |

## Paper Reference
For academic referencing, please cite the underlying orchestration framework:

```bibtex
@inproceedings{evolving_orchestration2025,
  title={Multi-Agent Collaboration via Evolving Orchestration},
  author={Anonymous Authors},
  booktitle={Proceedings of the 39th Conference on Neural Information Processing Systems (NeurIPS 2025)},
  year={2025}
}
```

## Authors
* **Amal Tom Ajith** — Core Engine & Research Extensions
* **Roshan** — Application Layer & Automation Platform
* *Christ University, Bengaluru — B.Tech IT 2023-2027*
