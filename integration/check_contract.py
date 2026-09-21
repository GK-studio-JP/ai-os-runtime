"""Exercise the locked control plane without network writes or model calls."""
import argparse
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def check(root):
    runtime_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "ai-os-context" / "src"))
    sys.path.insert(0, str(runtime_root))
    sys.path.insert(0, str(root / "ai-os-runtime-browser-worker"))
    from ai_os_context import REPLAY_CONTRACT
    from ai_os_context.capsule import build_capsule
    from ai_os_context.replay import replay
    from ai_os_context.views import scheduler_row, scheduler_view
    from browser_worker_launcher import canonical_claim_present, canonical_result_present

    runtime = module("tested_runtime", runtime_root / "runtime.py")
    scheduler = module("tested_scheduler", root / "ai-os-scheduler" / "scheduler.py")
    kernel = module("tested_kernel", root / "ai-os-kernel" / "kernel.py")
    registry = json.loads((root / "ai-os-kernel" / "registry/processes.json").read_text())
    assert REPLAY_CONTRACT == "actor-binding-v1"
    t0 = datetime(2026, 9, 21, tzinfo=timezone.utc)
    envelope = dict(process="PROC-RUNTIME", repository="GK-studio-JP/ai-os-runtime",
                    objective="Verify identity", priority=1, contracts=[], context_refs=[])
    issue = dict(number=1, title="Identity integration", author_association="OWNER",
                 user={"login": "alice"}, html_url="https://github.com/GK-studio-JP/ai-bulletin-board/issues/1",
                 body="<!-- ai-os-task:v1 -->\n```json\n" + json.dumps(envelope) + "\n```")

    def event(cid, kind, actor):
        when = (t0 + timedelta(seconds=cid)).isoformat()
        payload = dict(type=kind, agent_id="shared-agent", task="#1", idempotency_key=str(cid),
                       summary="integration", next_action=None if kind == "RESULT" else "continue", artifacts=[])
        return dict(id=cid, created_at=when, updated_at=when, user={"login": actor},
                    author_association="MEMBER", body="<!-- ai-bb:v1 -->\n" + json.dumps(payload))

    open_state = replay(issue, [], now=t0)
    cap = build_capsule(issue, open_state, generated_at=t0)
    view = scheduler_view("GK-studio-JP/ai-bulletin-board", [scheduler_row(issue, open_state)], generated_at=t0)
    plan = scheduler.build_plan(view)
    assert kernel.validate_dispatch(registry, plan)["valid"]
    dispatch = plan["dispatches"][0]
    dispatch["context"] = dict(fingerprint=cap["fingerprint"])
    boot = dict(schema="ai-os-worker-boot:v1", authoritative=False, persist_required=True,
                source_plan_fingerprint=plan["fingerprint"], dispatch_count=1,
                dispatch=dispatch, capsule=dict(fingerprint=cap["fingerprint"]))
    args = dict(worker_id="shared-agent", worker_actor="alice")
    assert runtime.preflight(boot, cap, open_state.to_dict(), **args)["status"] == "CLAIM_REQUIRED"

    rows = [event(1, "CLAIM", "alice")]
    now = t0 + timedelta(seconds=10)
    claimed = replay(issue, rows, now=now)
    cap = build_capsule(issue, claimed, generated_at=now)
    boot["capsule"]["fingerprint"] = dispatch["context"]["fingerprint"] = cap["fingerprint"]
    fresh = claimed.to_dict()
    ready = runtime.preflight(boot, cap, fresh, **args)
    assert ready["status"] == "READY"
    assert runtime.preflight(boot, cap, fresh, worker_id="shared-agent", worker_actor="bob")["status"] != "READY"
    inv = runtime.prepare(boot, cap, ready, driver="manual")
    result = dict(schema="ai-os-worker-result:v1", invocation_fingerprint=inv["fingerprint"],
                  worker_id="shared-agent", worker_actor="alice", status="completed", summary="verified",
                  next_action=None, artifacts=[], requests=[])
    outcome = runtime.normalize(inv, result)
    assert runtime.gate(boot, fresh, outcome)["eligible_for_persistence"]
    for actor in (None, "bob"):
        assert not runtime.gate(boot, {**fresh, "owner_actor": actor}, outcome)["eligible_for_persistence"]
    assert canonical_claim_present(rows, task="#1", agent_id="shared-agent", actor_login="alice", now=now)
    assert not canonical_claim_present(rows, task="#1", agent_id="shared-agent", actor_login="bob", now=now)
    forged = rows + [event(2, "RESULT", "bob")]
    assert replay(issue, forged, now=now).state == "claimed"
    rows.append(event(3, "RESULT", "alice"))
    assert canonical_result_present(rows, task="#1", agent_id="shared-agent", actor_login="alice", now=now)
    assert not canonical_result_present(rows, task="#1", agent_id="shared-agent", actor_login="bob", now=now)
    print("CONTROL_PLANE_ACTOR_BINDING_OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True, help="Directory containing the four dependency checkouts")
    check(parser.parse_args().root.resolve())
