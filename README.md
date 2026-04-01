# How and Why Agents Can Identify Bug-Introducing Commits

Code and Data for the paper "How and Why Agents Can Identify Bug-Introducing Commits" (Currently Under Review).

## Citation

If you use this work, please cite:

```bibtex
@misc{risse2026simpleszzagent,
      title={How and Why Agents Can Identify Bug-Introducing Commits},
      author={Niklas Risse and Marcel Böhme},
      year={2026},
      eprint={2603.29378},
      archivePrefix={arXiv},
      primaryClass={cs.SE},
      url={https://arxiv.org/abs/2603.29378},
}
```

## Repository Structure

```
├── README.md
├── requirements.txt              # Python dependencies
├── .env.template                 # API key template
├── clone_repos.sh                # Script to clone all 153 required repositories
├── generate_all_tables_and_figures.sh  # Regenerate all tables/figures (no API calls)
├── src/                          # Source code
│   ├── simple_szz_agent.py       # Simple-SZZ-Agent implementation
│   ├── szz_agent_stage_01.py     # SZZ-Agent Stage 1 (candidate identification)
│   ├── szz_agent_stage_02.py     # SZZ-Agent Stage 2 (candidate selection)
│   ├── prompts.py                # LLM prompt templates
│   ├── evaluation_utils.py       # Evaluation metric computation
│   ├── statistical_comparison.py # Statistical significance tests
│   ├── collect_DS_LINUX-26.py    # Data collection script
│   └── baselines/                # Baseline implementations
│       ├── baseline_bszz.py      # B-SZZ (Basic SZZ)
│       ├── baseline_agszz.py     # AG-SZZ
│       ├── baseline_lszz.py      # L-SZZ
│       ├── baseline_rszz.py      # R-SZZ
│       ├── baseline_maszz.py     # MA-SZZ
│       ├── baseline_raszz.py     # RA-SZZ
│       ├── baseline_vszz.py      # V-SZZ
│       ├── baseline_llm4szz.py   # LLM4SZZ
│       ├── pyszz_v2/             # PySZZ library
│       ├── llm4szz/              # LLM4SZZ library
│       └── vszz/                 # V-SZZ library
├── data/                         # Raw datasets
│   ├── DS_GITHUB.json
│   ├── DS_LINUX.json
│   └── DS_LINUX-26.json
├── sampled_datasets/             # Sampled datasets used in experiments
│   ├── DS_LINUX_100_42.json
│   ├── DS_LINUX_200_42.json
│   ├── DS_LINUX-26_100_42.json
│   ├── DS_GITHUB-c_100_42.json
│   ├── DS_GITHUB-j.json
│   ├── generate_samples.sh       # Reproduce sampling
│   └── generate_DS_*.py          # Individual sampling scripts
├── repos/                        # Cloned repositories (not included, see Setup)
└── experiments/                  # Experiment directories
    ├── rq1_effectiveness/        # Table 1: SZZ-Agent effectiveness
    ├── rq2a_data_leakage/        # Table 2: Data leakage analysis
    ├── rq2b_llm_backbone/        # Table 3: LLM backbone comparison
    ├── rq2c_context_ablation/    # Table 4: Context ablation study
    ├── rq2d_stage_analysis/      # Table 5: Stage 1 vs Stage 2
    ├── rq2e_threshold/           # Table 6: Threshold analysis
    ├── rq3_simple_agent/         # Table 7: Simple-SZZ-Agent comparison
    ├── rq4a_behavior_analysis/   # Figures 3-5: Behavior analysis
    ├── rq4b_error_analysis/      # Figure 6: Error analysis
    └── rq4c_models_agents/       # Table 8: Model and agent comparison
```

## Important Notes

> [!WARNING]
> Running all agent experiments from scratch requires significant API costs (~$800-1,800 USD) and time (~5-10 days). All pre-computed results are included in each experiment's `results/` directory. You can regenerate tables and figures without any API calls.

> [!TIP]
> Pass `--quick` to any `run_experiment.sh` to run with only 2 samples per dataset. This verifies the pipeline works correctly with minimal cost (~$20-40 total, ~3 hours).

## Prerequisites

- Python 3.11+ (we used 3.11.14)
- Git
- Java 11+ (for V-SZZ and RA-SZZ baselines; optional, baselines are skipped if not installed; we used openjdk 17.0.17)

## Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp .env.template .env
# Edit .env and add your API keys
```

| Key                  | Required for                                   |
| -------------------- | ---------------------------------------------- |
| `ANTHROPIC_API_KEY`  | SZZ-Agent experiments (RQ1, RQ2a-e, RQ3, RQ4c) |
| `OPENAI_API_KEY`     | LLM4SZZ baseline (RQ2b)                        |
| `OPENROUTER_API_KEY` | OpenHands experiments (RQ4c)                   |

### 3. Install Claude Code

Our experiments used **Claude Code v2.1.81**. Either follow the [Claude Code installation guide](https://code.claude.com/docs/en/quickstart) or run this to install the specific version:

```bash
curl -fsSL https://claude.ai/install.sh | bash -s 2.1.81
```

### 4. Clone required repositories

Clone all required repositories using the provided script (153 repos total):

```bash
# Preview what will be cloned
bash clone_repos.sh --dry-run

# Clone all repositories
bash clone_repos.sh
```

The script shows progress (`[N/TOTAL]`), skips repos that already exist, and reports a summary at the end.

> [!NOTE]
> This requires ~100+ GB of disk space. Large repos (linux, qemu, cpython, mesa) take significant time to clone.

### 5. Install OpenHands (RQ4c only)

Our experiments used **OpenHands v1.13.1**. Either follow the [OpenHands installation guide](https://docs.openhands.dev/openhands/usage/cli/installation#executable-binary) or run this to install the specific version:

```bash
# macOS (Apple Silicon)
sudo mkdir -p /usr/local/bin
sudo curl -fsSL https://github.com/OpenHands/OpenHands-CLI/releases/download/1.13.1/openhands-macos-arm64 -o /usr/local/bin/openhands && sudo chmod +x /usr/local/bin/openhands

# macOS (Intel)
sudo curl -fsSL https://github.com/OpenHands/OpenHands-CLI/releases/download/1.13.1/openhands-macos-intel -o /usr/local/bin/openhands && sudo chmod +x /usr/local/bin/openhands

# Linux (x86_64)
sudo curl -fsSL https://github.com/OpenHands/OpenHands-CLI/releases/download/1.13.1/openhands-linux-x86_64 -o /usr/local/bin/openhands && sudo chmod +x /usr/local/bin/openhands

# Linux (ARM64)
sudo curl -fsSL https://github.com/OpenHands/OpenHands-CLI/releases/download/1.13.1/openhands-linux-arm64 -o /usr/local/bin/openhands && sudo chmod +x /usr/local/bin/openhands
```

### 6. Unzip baseline source code (optional)

The baseline implementations are shipped as zip archives in `src/baselines/`. Unzip them before running any baseline:

```bash
cd src/baselines
unzip llm4szz.zip
unzip pyszz_v2.zip
unzip vszz.zip
cd ../..
```

### 7. Install srcml (optional)

[srcml](https://www.srcml.org/) (our experiments used **v1.1.0**) is used for comment filtering in the AG-SZZ, L-SZZ, R-SZZ, MA-SZZ, and RA-SZZ baselines. Install it following the [srcml installation guide](https://www.srcml.org/#download). Baselines still run without it but skip comment filtering, which may cause minor result differences.

## Dataset Sampling

The sampled datasets are included in `sampled_datasets/`. To reproduce the sampling:

```bash
cd sampled_datasets
bash generate_samples.sh
```

This uses `seed=42` for deterministic sampling and produces identical output.

## Experiments

Each experiment has a self-contained directory under `experiments/` with:

- `run_experiment.sh` - Runs the full experiment
- `generate_table.py` or `generate_figure.py` - Generates LaTeX output from results
- `results/` - Pre-computed results (included)
- `tables/` or `figures/` - Generated LaTeX output (included)

### Regenerating Tables and Figures Only (No API Calls)

All experiment results are included in each experiment's `results/` directory. To regenerate all tables and figures from pre-computed results:

```bash
bash generate_all_tables_and_figures.sh
```

### RQ1: SZZ-Agent Effectiveness (Table 1)

Evaluates SZZ-Agent on DS_LINUX (200 samples), DS_GITHUB-c (100 samples), and DS_GITHUB-j (75 samples).

```bash
bash experiments/rq1_effectiveness/run_experiment.sh                     # Full run (~$300-600, ~2-4 days)
bash experiments/rq1_effectiveness/run_experiment.sh --baselines         # Full run including baselines
bash experiments/rq1_effectiveness/run_experiment.sh --quick             # Quick check (2 samples per dataset)
python experiments/rq1_effectiveness/generate_table.py                   # Table only (no API calls)
```

Pass `--baselines` to also run all baselines (B-SZZ, AG-SZZ, L-SZZ, R-SZZ, MA-SZZ, RA-SZZ, V-SZZ, LLM4SZZ). V-SZZ is automatically skipped if Java is not installed; LLM4SZZ is skipped if `OPENAI_API_KEY` is not set.

### RQ2a: Data Leakage Analysis (Table 2)

Evaluates SZZ-Agent on DS_LINUX-26 (commits after LLM training cutoff).

```bash
bash experiments/rq2a_data_leakage/run_experiment.sh           # Full run
bash experiments/rq2a_data_leakage/run_experiment.sh --quick   # Quick check
python experiments/rq2a_data_leakage/generate_table.py         # Table only (no API calls)
```

### RQ2b: LLM Backbone Comparison (Table 3)

Compares LLM4SZZ with different LLM backends against SZZ-Agent.

```bash
bash experiments/rq2b_llm_backbone/run_experiment.sh           # Full run
bash experiments/rq2b_llm_backbone/run_experiment.sh --quick   # Quick check
python experiments/rq2b_llm_backbone/generate_table.py         # Table only (no API calls)
```

### RQ2c: Context Ablation Study (Table 4)

Ablation study: SZZ-Agent with/without fix commit message and diff.

```bash
bash experiments/rq2c_context_ablation/run_experiment.sh           # Full run
bash experiments/rq2c_context_ablation/run_experiment.sh --quick   # Quick check
python experiments/rq2c_context_ablation/generate_table.py         # Table only (no API calls)
```

### RQ2d: Stage Analysis (Table 5)

Compares Stage 1 only, Stage 2 only, and combined SZZ-Agent.

```bash
bash experiments/rq2d_stage_analysis/run_experiment.sh           # Full run
bash experiments/rq2d_stage_analysis/run_experiment.sh --quick   # Quick check
python experiments/rq2d_stage_analysis/generate_table.py         # Table only (no API calls)
```

### RQ2e: Threshold Analysis (Table 6)

Evaluates different candidate selection thresholds (8, 32, 128, 512, infinity).

```bash
bash experiments/rq2e_threshold/run_experiment.sh           # Full run
bash experiments/rq2e_threshold/run_experiment.sh --quick   # Quick check
python experiments/rq2e_threshold/generate_table.py         # Table only (no API calls)
```

### RQ3: Simple-SZZ-Agent Comparison (Table 7)

Compares Simple-SZZ-Agent against SZZ-Agent and baselines.

```bash
bash experiments/rq3_simple_agent/run_experiment.sh           # Full run
bash experiments/rq3_simple_agent/run_experiment.sh --quick   # Quick check
python experiments/rq3_simple_agent/generate_table.py         # Table only (no API calls)
```

### RQ4a: Behavior Analysis (Figures 3-5)

Runs Simple-SZZ-Agent on DS_LINUX-26, then generates scatter plots and bar charts analyzing agent behavior.

```bash
bash experiments/rq4a_behavior_analysis/run_experiment.sh           # Full run
bash experiments/rq4a_behavior_analysis/run_experiment.sh --quick   # Quick check
python experiments/rq4a_behavior_analysis/generate_figures.py       # Figures only (no API calls)
```

### RQ4b: Error Analysis (Figure 6)

Runs Simple-SZZ-Agent with Sonnet on DS_LINUX-26, then generates Sankey diagram of error categories.

```bash
bash experiments/rq4b_error_analysis/run_experiment.sh           # Full run
bash experiments/rq4b_error_analysis/run_experiment.sh --quick   # Quick check
python experiments/rq4b_error_analysis/generate_figure.py        # Figure only (no API calls)
```

Reproduction only reruns the agent; the error analysis itself was performed manually and the figure is always generated from the original results. See [`experiments/rq4b_error_analysis/error_analysis_report.txt`](experiments/rq4b_error_analysis/error_analysis_report.txt) for details on the manual analysis.

### RQ4c: Model and Agent Comparison (Table 8)

Compares Simple-SZZ-Agent across different models (Haiku, Sonnet, Opus) and agents (Claude Code, OpenHands).

```bash
bash experiments/rq4c_models_agents/run_experiment.sh           # Full run
bash experiments/rq4c_models_agents/run_experiment.sh --quick   # Quick check
python experiments/rq4c_models_agents/generate_table.py         # Table only (no API calls)
```
