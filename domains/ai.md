# Domain supplement — AI / ML

Write a **도메인 보충: AI/ML** section covering the items below that the paper
addresses. Pull every number verbatim with a reference tag. Omit items the paper
genuinely does not cover (say so briefly rather than guessing).

## Model & method
- **Architecture** — components, key design choices, what's new vs. a standard
  baseline. Parameter count / model sizes studied.
- **Inputs/outputs & objective** — task formulation, loss/training objective.

## Training setup
- **Data** — datasets, sizes, splits, preprocessing, tokenization; data sources
  and any licensing/contamination concerns.
- **Optimization** — optimizer, LR schedule, batch size, # steps/epochs, key
  hyperparameters.
- **Compute** — hardware, # accelerators, wall-clock / GPU-hours, est. cost.

## Evaluation
- **Benchmarks & metrics** — which datasets/metrics, and against which baselines.
- **Headline results** — the comparison table's key numbers; SOTA delta.
- **Ablations** — which components actually matter, per the ablation study.
- **Efficiency** — FLOPs, throughput, latency, params vs. quality trade-off.

## Reproducibility & risk
- **Artifacts** — code, weights, configs released? license?
- **Robustness/limitations** — generalization, distribution shift, evaluation
  validity, fairness/bias, safety concerns the paper raises (or omits — mark
  omissions as `(해석)`).
