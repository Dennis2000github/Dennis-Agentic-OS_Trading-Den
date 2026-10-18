# BUDGET — Resource and Iteration Controls

```yaml
max_iterations_per_step: 5
max_agent_runs_per_iteration: 2
max_total_agent_runs: 20
stop_on_repeated_failure: 3
require_human_review_after: 5
```

If the budget is exhausted, the controller MUST STOP and request human intervention.
