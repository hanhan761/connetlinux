#!/usr/bin/env bash
set -uo pipefail

if [[ $# -ne 1 ]]; then
  printf 'usage: yun_job_runner.sh JOB_DIR\n' >&2
  exit 64
fi

job_dir=$1
case "$job_dir" in
  "$HOME"/.yun/jobs/*) ;;
  *)
    printf 'refusing job directory outside ~/.yun/jobs: %s\n' "$job_dir" >&2
    exit 65
    ;;
esac

cd "$job_dir" || exit 66
umask 077
mkdir -p results

child_pid=''
finish() {
  rc=$?
  trap - EXIT HUP INT TERM
  date -u +%Y-%m-%dT%H:%M:%SZ > finished_at
  printf '%s\n' "$rc" > exit_code
  if [[ -f cancel_requested ]]; then
    printf 'cancelled\n' > status
  elif [[ $rc -eq 0 ]]; then
    printf 'succeeded\n' > status
  else
    printf 'failed\n' > status
  fi
}

terminate() {
  : > cancel_requested
  if [[ "$child_pid" =~ ^[0-9]+$ ]] && kill -0 "$child_pid" 2>/dev/null; then
    child_sid=$(ps -o sid= -p "$child_pid" 2>/dev/null | tr -d ' ')
    if [[ "$child_sid" == "$child_pid" ]]; then
      kill -TERM -- "-$child_pid" 2>/dev/null || true
    else
      kill -TERM "$child_pid" 2>/dev/null || true
    fi
  fi
  exit 143
}

trap finish EXIT
trap terminate HUP INT TERM

date -u +%Y-%m-%dT%H:%M:%SZ > started_at
printf 'running\n' > status
printf '%s\n' "$$" > runner_pid

export YUN_JOB_ID=${PWD##*/}
export YUN_JOB_DIR=$PWD
export YUN_RESULTS_DIR=$PWD/results

setsid bash ./job.sh > stdout.log 2> stderr.log &
child_pid=$!
printf '%s\n' "$child_pid" > child_pid
wait "$child_pid"
exit $?
