# 远程计算闭环

Use this branch only for a target registered with the `compute` role. It adds
remote Bash, tmux, and setsid to the ordinary PEM/OpenSSH path. The first public
backend is detached `tmux`; do not claim Slurm/Kubernetes support.

## Job contract

Submit a local Bash script. The runner creates:

```text
~/.yun/jobs/JOB_ID/
├── job.sh
├── status
├── started_at
├── finished_at
├── exit_code
├── stdout.log
├── stderr.log
└── results/
```

The script receives `YUN_JOB_ID`, `YUN_JOB_DIR`, and `YUN_RESULTS_DIR`. Write
deliverables beneath `$YUN_RESULTS_DIR`. Keep credentials out of scripts and
arguments; use an already-configured remote secret mechanism when required.

## Commands

```text
python scripts/yunctl.py probe TARGET
python scripts/yunctl.py submit TARGET ./job.sh --name experiment
python scripts/yunctl.py jobs TARGET
python scripts/yunctl.py status TARGET JOB_ID
python scripts/yunctl.py logs TARGET JOB_ID --lines 120
python scripts/yunctl.py fetch TARGET JOB_ID ./results
python scripts/yunctl.py cancel TARGET JOB_ID
python scripts/yunctl.py cleanup TARGET JOB_ID --confirm-job JOB_ID
```

For a protected target, add `--confirm-target TARGET` to `submit`, `cancel`, and
`cleanup`.

## Completion loop

1. Probe CPU/GPU, memory, disk, tmux, input existence, expected runtime, and
   output size.
2. Make the job deterministic and restart-safe where practical.
3. Submit once and retain the returned job ID.
4. Poll `status` proportionally to expected runtime; inspect redacted `logs` only
   when progress or diagnosis requires it.
5. On `succeeded`, require exit code 0 and fetch/verify every requested artifact.
   On `failed`, preserve the directory and report the first actionable error. On
   `cancelled`, verify both status and absent session.
6. Never resubmit a failed job unchanged. Diagnose, correct, and create a new ID.
7. Clean a terminal job only after its needed results are safe. `cleanup`
   requires the exact ID twice and refuses an active session.
