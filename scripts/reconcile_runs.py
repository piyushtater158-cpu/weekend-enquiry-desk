"""Mark agent_runs rows orphaned by a process restart as errored.

A run's agent_runs.status stays 'running' in the database for its entire
lifetime unless something explicitly finishes it: either reporting() at the
end of a normal graph.invoke() call, or a retry-crm/demo-ghl-sync call (see
app/main.py's _sync_existing_lead). A run legitimately parked at the
human-approval interrupt is also still 'running' in the database, for as
long as an operator takes to decide - that is correct, not a bug (see
_show_draft_slot's docstring in app/main.py, and the command centre's own
reconcile-on-read fix for the UI side of this).

The case this script fixes: a run where graph.invoke() was in-flight inside a
FastAPI BackgroundTask when the process restarted (deploy, crash, `docker
compose up --build`). No in-memory call is left to ever resume it, and its
checkpoint shows no pending interrupt. Those rows otherwise sit in the queue
forever looking identical to a healthy in-progress run. This finds them by
actually asking the checkpointer, not by guessing from age alone, and reports
a plan before touching anything unless told to apply it.

Usage (run as a module, from the repo root, so `from app import repo` resolves -
a plain `python scripts/reconcile_runs.py` puts scripts/ on sys.path instead of
the repo root and fails with ModuleNotFoundError: No module named 'app'):
  .venv/Scripts/python.exe -m scripts.reconcile_runs            # dry run, prints the plan
  .venv/Scripts/python.exe -m scripts.reconcile_runs --apply    # actually marks them errored
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from app import repo
from app.graph import build_production_graph
from app.main import _snapshot_interrupted

# A run has to be older than this before it's eligible for reconciliation - a
# run that started seconds ago and isn't interrupted yet is most likely just
# mid-flight in its own background task, not orphaned. Matches the command
# centre's own "stale" threshold (app/main.py's _age_minutes) so this script
# and the UI agree on what counts as old enough to be suspicious.
_MIN_AGE_MINUTES = 15


def _is_old_enough(started_at) -> bool:
    if started_at is None:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started_at > timedelta(minutes=_MIN_AGE_MINUTES)


def main(apply: bool) -> None:
    graph, checkpointer = build_production_graph()
    try:
        running = repo.list_running_runs()
        print(f"{len(running)} run(s) currently marked 'running'")
        for run in running:
            run_id, thread_id = run["run_id"], run["thread_id"]
            if not _is_old_enough(run["started_at"]):
                print(f"  skip      {run_id}  thread={thread_id}  (too recent to judge)")
                continue

            config = {"configurable": {"thread_id": thread_id}}
            snapshot = graph.get_state(config)
            if _snapshot_interrupted(snapshot):
                print(f"  keep      {run_id}  thread={thread_id}  (parked at approval interrupt)")
                continue

            verb = "would mark" if not apply else "marking"
            print(f"  {verb:<9} {run_id}  thread={thread_id}  (no pending interrupt - orphaned)")
            if apply:
                repo.finish_agent_run(
                    run_id=run_id, status="error", error_kind="orphaned_run",
                    error_detail="no pending interrupt found on reconcile - likely a "
                                 "process restart while this run was in flight",
                )
        if not apply:
            print("\nDry run - nothing was changed. Re-run with --apply to mark these runs errored.")
    finally:
        checkpointer.conn.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
