#!/usr/bin/env bash

set -o errexit
set -o pipefail
set -o nounset

PROJECT_ROOT="/root/ooir.lean"
LOG_DIR="$PROJECT_ROOT/logs"
TIMESTAMP="$(date -u +"%Y%m%d_%H%M%S")"
LOG_FILE="$LOG_DIR/orchestrator_${TIMESTAMP}.log"

mkdir -p "$LOG_DIR"

start_time_epoch=$(date +%s)

log() {
  local level="$1"; shift
  local msg="$*"
  local now
  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "[$now] [$level] $msg" | tee -a "$LOG_FILE"
}

run_step() {
  local name="$1"; shift
  local cmd=("$@")
  local step_start
  step_start=$(date +%s)

  log INFO "===== START $name ====="
  log INFO "Command: ${cmd[*]}"

  # Run command streaming stdout+stderr to both console and log
  if "${cmd[@]}" 2>&1 | tee -a "$LOG_FILE"; then
    local step_end
    step_end=$(date +%s)
    local duration=$(( step_end - step_start ))
    log INFO "===== DONE $name in ${duration}s ====="
  else
    local exit_code=$?
    local step_end
    step_end=$(date +%s)
    local duration=$(( step_end - step_start ))
    log ERROR "===== FAILED $name (exit $exit_code) after ${duration}s ====="
    exit "$exit_code"
  fi
}

cd "$PROJECT_ROOT"

# Steps
run_step "fetch"    python3 -u "$PROJECT_ROOT/src/pipeline_fetch.py"
run_step "summarize" python3 -u "$PROJECT_ROOT/src/pipeline_summarize.py"
run_step "tts"       python3 -u "$PROJECT_ROOT/src/pipeline_tts.py"
run_step "notify"    python3 -u "$PROJECT_ROOT/src/pipeline_notify.py"

total_end_epoch=$(date +%s)
total_duration=$(( total_end_epoch - start_time_epoch ))
log INFO "ALL STEPS COMPLETED in ${total_duration}s"

exit 0


