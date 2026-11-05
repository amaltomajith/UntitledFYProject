"""Standard reasoning benchmarks for the thesis core (core.Orchestrator).

datasets.py  fetch and freeze fixed-seed subsets of GSM8K, GSM-Hard, MMLU-Pro, BBH (and GAIA, if a token allows)
checkers.py  answer checkers, one per dataset
client.py    CountingClient: counts every call, the policy's included
methods.py   direct, cot, fixed pipeline, puppeteer
run.py       the runner (resume, provider errors excluded)
stats.py     accuracy with Wilson CIs, tokens, cost, paired McNemar tests
"""
