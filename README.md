# ai-os-runtime

`ai-os-runtime` is the provider-neutral execution runtime for the GitHub-native AI microkernel OS.

It sits **after** Scheduler selection and Kernel identity validation. It does not decide task priority, grant capabilities, or make GitHub state authoritative by itself.

```text
GK-studio-JP/ai-bulletin-board
        |
        v
 ai-os-context
        |
        v
ai-os-scheduler
        |
        v
 ai-os-kernel
        |
        v
 ai-os-runtime
   |         \
   |          +--> Compute Driver (LLM / manual / API)
   |
   +--> canonical event proposal
             |
             v
     Kernel + persistence driver
```

## Runtime loop

The v0.1 runtime implements four deterministic boundaries:

1. `preflight`: compare a Worker boot bundle with a **fresh canonical replay**.
2. `prepare`: when ownership is confirmed, build a bounded `ai-os-worker-invocation:v1`.
3. `normalize`: validate a structured Worker result and convert it into a non-authoritative runtime outcome.
4. `gate`: compare the outcome with another **fresh canonical replay** before producing a persistable canonical event proposal.

The Runtime never treats an LLM's statement that work is complete as completion. A `RESULT` remains only a proposal until an authorized persistence driver appends it to the canonical Issue and a later replay confirms it.

## Preflight states

`preflight` fails closed:

- `CLAIM_REQUIRED`: task is open and can only proceed after a CLAIM is persisted.
- `READY`: task is claimed by the requested Worker and the boot context is still current.
- `WAIT`: another Worker owns the live lease.
- `STALE_CONTEXT`: the canonical Issue has changed since the capsule was built.
- `STOP`: task is completed or history is unsafe.

Example:

```bash
python runtime.py preflight \
  --boot worker/boot.json \
  --capsule worker/capsule.json \
  --fresh runtime/fresh-replay.json \
  --worker-id worker-001 \
  --output runtime/preflight.json
```

If `CLAIM_REQUIRED`, the output contains a deterministic `CLAIM` proposal. Persisting it is outside Runtime authority.

## Worker invocation

After a fresh replay confirms the same Worker owns the task:

```bash
python runtime.py prepare \
  --boot worker/boot.json \
  --capsule worker/capsule.json \
  --preflight runtime/preflight.json \
  --driver manual \
  --output runtime/invocation.json
```

The invocation contains only the selected dispatch and its single Context Capsule. It is provider-neutral: a manual ChatGPT session, API model, or another compute provider can consume the same envelope.

## Compute driver adapter

`driver_runner.py` provides the first replaceable compute boundary without making any provider authoritative. It launches one operator-selected subprocess, writes the complete `ai-os-worker-invocation:v1` JSON to that process on stdin, and requires exactly one `ai-os-worker-result:v1` JSON object on stdout.

Before returning a result, the runner checks the invocation fingerprint and Worker identity. A non-zero driver exit fails closed, and driver stderr is not copied into the Runtime error message because provider adapters may use stderr for diagnostic data that must not leak into coordination state.

Example:

```bash
python driver_runner.py run \
  --invocation runtime/invocation.json \
  --output worker-result.json \
  -- python path/to/provider_adapter.py

python runtime.py normalize \
  --invocation runtime/invocation.json \
  --result worker-result.json \
  --output runtime/outcome.json
```

The adapter is intentionally outside Kernel authority. It may call an LLM, a manual bridge, or another compute service, but it must return the structured result contract and must not mutate the canonical bulletin board directly.

## Execution budgets

A dispatch may optionally carry a non-authoritative execution budget. Runtime
normalizes that budget into the Worker invocation; absence of the field preserves
the existing v0.1 invocation contract.

```json
{
  "schema": "ai-os-execution-budget:v1",
  "authoritative": false,
  "deadline_seconds": 1800,
  "max_steps": 100,
  "max_tool_calls": 50,
  "max_tokens": null
}
```

`driver_runner.py` hard-enforces `deadline_seconds` at the subprocess boundary.
The effective wall-clock timeout is the smaller of the operator timeout and the
budget deadline. A budget deadline timeout fails closed as
`execution-budget / compute`.

Active step, tool-call, or token limits require the compute adapter to return
non-authoritative execution accounting:

```json
{
  "schema": "ai-os-execution-usage:v1",
  "authoritative": false,
  "steps": 37,
  "tool_calls": 14,
  "tokens": null
}
```

Missing usage for an active counter limit, or a reported value above its limit,
fails closed before the result can become a Runtime outcome. `normalize` repeats
the same check so bypassing the subprocess runner does not bypass the budget
contract.

In this first version, step/tool/token counters are adapter-reported contract
data rather than independently observed Runtime measurements. They are not
authority, capability grants, acceptance evidence, or canonical state. Future
tool adapters and receipts can provide stronger Runtime-observed accounting
without changing the budget schema.

Budget fields cannot change task ownership, Scheduler priority, Kernel
capabilities, or persistence eligibility. Scheduler integration is optional:
dispatches that do not carry `execution_budget` continue to use the existing
Runtime behavior.

## Worker result

A compute driver returns:

```json
{
  "schema": "ai-os-worker-result:v1",
  "invocation_fingerprint": "sha256:...",
  "worker_id": "worker-001",
  "status": "completed",
  "summary": "Implemented and tested the requested change.",
  "next_action": null,
  "artifacts": ["commit:abc123"],
  "requests": []
}
```

Supported statuses are `progress`, `completed`, `blocked`, `page_fault`, and `failed`.

Normalize it:

```bash
python runtime.py normalize \
  --invocation runtime/invocation.json \
  --result worker-result.json \
  --output runtime/outcome.json
```

## Postflight gate

Before persistence, replay the canonical Issue again and gate the outcome:

```bash
aios-context replay \
  --repo GK-studio-JP/ai-bulletin-board \
  --issue 123 \
  --output runtime/fresh-after.json

python runtime.py gate \
  --boot worker/boot.json \
  --fresh runtime/fresh-after.json \
  --outcome runtime/outcome.json \
  --output runtime/gate.json
```

`eligible_for_persistence=true` means only that Runtime's deterministic safety checks passed. It is **not** a capability grant. Kernel authorization and a persistence driver are still required.

## Invariants

- `GK-studio-JP/ai-bulletin-board` is the live canonical coordination journal.
- Context Capsules, dispatch plans, boot bundles, invocations, outcomes, and gates are non-authoritative.
- A Worker cannot execute from stale context.
- A Worker cannot persist a result without a live matching ownership lease.
- A result never completes a task by itself.
- Missing context becomes `page_fault`; it is not guessed.
- Compute providers are replaceable.
- Runtime does not choose task priority and does not grant capabilities.

## Workflows

- `.github/workflows/test.yml` runs unit tests.
- `.github/workflows/live-preflight.yml` rebuilds the current control-plane projection from the canonical board and runs Runtime preflight for at most one selected task. It never mutates the board.
