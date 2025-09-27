#!/usr/bin/env python3
"""
Utility functions per MVP - logging semplice (live terminal)
"""
import datetime
import os
import time

RUN_ID = os.environ.get("RUN_ID") or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def set_run_id(run_id: str) -> None:
    """Override globale del run_id per correlare i log di una singola esecuzione."""
    global RUN_ID
    if run_id:
        RUN_ID = run_id


def log_info(msg: str) -> None:
    """Log messaggio info (live su terminale)"""
    print(f"[{_now_str()}] [INFO] [run_id={RUN_ID}] {msg}")

def log_error(msg: str) -> None:
    """Log messaggio errore (live su terminale)"""
    print(f"[{_now_str()}] [ERROR] [run_id={RUN_ID}] {msg}")


def log_step_start(step_name: str) -> float:
    """Stampa inizio step e ritorna il time marker per calcolare la durata."""
    log_info(f"{step_name}: start")
    return time.perf_counter()


def log_step_end(step_name: str, start_time: float, extra: str | None = None) -> None:
    """Stampa fine step con durata in secondi e dettagli aggiuntivi opzionali."""
    elapsed = time.perf_counter() - start_time
    suffix = f" {extra}" if extra else ""
    log_info(f"{step_name}: done in {elapsed:.2f}s{suffix}")


def get_env(name: str, env_path: str = 'secrets/.env', default: str | None = None) -> str:
    """Legge una variabile da secrets/.env oppure dall'ambiente.

    Precedenza: os.environ -> file .env -> default.
    """
    # 1) Ambiente
    if name in os.environ and os.environ[name]:
        return os.environ[name]

    # 2) File .env (risolto rispetto alla root progetto, non CWD)
    try:
        resolved_env_path = env_path
        if not os.path.isabs(resolved_env_path):
            resolved_env_path = os.path.join(PROJECT_ROOT, resolved_env_path)
        with open(resolved_env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith(name + '='):
                    value = line.split('=', 1)[1].strip()
                    if value.startswith(("'", '"')) and value.endswith(("'", '"')):
                        value = value[1:-1]
                    if value:
                        return value
    except FileNotFoundError:
        pass

    if default is not None:
        return default
    raise ValueError(f"Variabile {name} non trovata (env o {resolved_env_path if 'resolved_env_path' in locals() else env_path})")