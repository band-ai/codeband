# Configuration

`cb init --repo <url>` writes a default `codeband.yaml` designed for the free-tier Band.ai 10-agent cap. The default uses ten Band.ai agents plus one in-process Watchdog.

## Default `codeband.yaml`

```yaml
repo:
  url: https://github.com/myorg/myrepo.git
  branch: main
agents:
  conductor:
    framework: claude_sdk
    model: claude-sonnet-4-6
  mergemaster:
    framework: claude_sdk
    model: claude-sonnet-4-6
    test_command: null
    review_guidelines: null
    auto_merge: low
  planners:
    claude_sdk:
      count: 1
      model: claude-sonnet-4-6
      max_restarts: 5
      restart_delay_seconds: 5.0
    codex:
      count: 0
      model: null
      max_restarts: 5
      restart_delay_seconds: 5.0
  plan_reviewers:
    claude_sdk:
      count: 0
      model: null
      max_restarts: 5
      restart_delay_seconds: 5.0
    codex:
      count: 1
      model: gpt-5.5
      max_restarts: 5
      restart_delay_seconds: 5.0
    review_guidelines: null
  coders:
    claude_sdk:
      count: 1
      model: claude-opus-4-7
      max_restarts: 5
      restart_delay_seconds: 5.0
    codex:
      count: 1
      model: gpt-5.5
      max_restarts: 5
      restart_delay_seconds: 5.0
  reviewers:
    claude_sdk:
      count: 1
      model: claude-sonnet-4-6
      max_restarts: 5
      restart_delay_seconds: 5.0
    codex:
      count: 1
      model: gpt-5.5
      max_restarts: 5
      restart_delay_seconds: 5.0
    review_guidelines: null
  verifiers:
    claude_sdk:
      count: 1
      model: claude-opus-4-7
      max_restarts: 5
      restart_delay_seconds: 5.0
    codex:
      count: 1
      model: gpt-5.5
      max_restarts: 5
      restart_delay_seconds: 5.0
  watchdog:
    check_interval_seconds: 120
    stale_threshold_seconds: 300
    nudge_grace_seconds: 60
    nudge_suppression_seconds: 1800
    role_stale_thresholds:
      coder: 900
      mergemaster: 900
    swarm_idle_grace_seconds: 1800
    max_phase_visits: 10
    git_progress_check: true
    full_integrity_interval_patrols: 30
    transport_heal_enabled: true
    transport_pin_threshold_seconds: 1800
    transport_heal_max_attempts: 3
    merge_approval_backstop_seconds: 240
    merge_approval_backstop_max_renudges: 1
    acceptance_advance_backstop_seconds: 240
    acceptance_advance_max_renudges: 1
  handoff_verify_command: null
  required_verdicts: null
  allow_ungated_merge: false
  merge_approval: owner
  max_review_rounds: 6
  max_verify_attempts: 20
  max_rebase_rounds: 3
  verify_infra_exit_codes: null
  idle_resync_seconds: 30
  codex_turn_timeout_seconds: 3600
  max_message_retries: 3
  delivery: sdk
workspace:
  path: .codeband
  worktree_prefix: codeband
  mode: local
band:
  rest_url: https://app.band.ai
  ws_url: wss://app.band.ai/api/v1/socket/websocket
  memory_mode: auto
  liveness_mode: auto
```

`max_restarts` is deprecated and ignored at runtime, but current `cb init` output still emits it for every pool entry. Existing files may keep it; new behavior should be controlled with `restart_delay_seconds` and the reconnect-forever loop.

## Agent Count

The default pool is:

| Role | Count |
|------|------:|
| Conductor | 1 |
| Mergemaster | 1 |
| Planner | 1 |
| Plan Reviewer | 1 |
| Coders | 2 |
| Reviewers | 2 |
| Verifiers | 2 |

Total: 10 Band.ai agents. The Watchdog is an in-process daemon and does not use a Band.ai agent seat.

## Frameworks

| Framework | Backed by | Typical use |
|-----------|-----------|-------------|
| `claude_sdk` | Claude Code | Complex reasoning, refactoring, careful stepwise work |
| `codex` | Codex | Bulk generation, boilerplate, fast iteration |

Every role can use either framework. The default keeps Conductor and Mergemaster on Claude, pairs a Claude Planner with a Codex Plan Reviewer, and keeps one Coder, one Reviewer, and one Verifier from each framework.

## Cross-Model Pairing

Codeband enforces adversarial pairing through the agent prompts and Worker Pool Roster:

- Claude Coder PRs route to Codex Reviewers.
- Codex Coder PRs route to Claude Reviewers.
- Claude plans route to Codex Plan Reviewers.
- Codex plans route to Claude Plan Reviewers.
- Reviewed work routes to an opposite-framework Verifier for acceptance when a verifier is configured.

Coders dispatch first reviews directly to concrete reviewer display names, using deterministic worker-index pairing from the roster. For example, `Coder-Claude-1` prefers `Reviewer-Codex-1`; if only one Codex reviewer exists, it falls back to `Reviewer-Codex-0` and reports that reviewer capacity is shared. If an opposite-framework reviewer is unavailable, Codeband falls back to same-framework review with a warning. `cb doctor` warns when configuration makes cross-model pairing impossible or when reviewer capacity is lower than matching coder capacity.

Verifiers follow the same adversarial preference. The default config has one Claude Verifier and one Codex Verifier, so a passing review must clear a SHA-pinned `verify_acceptance` verdict before merge. Set both `agents.verifiers.{claude_sdk,codex}.count` values to `0` to opt out; tasks then merge from `review_passed`.

## Scaling

Use `cb scale` to adjust pool sizes:

```bash
cb scale coders.claude_sdk=2
cb scale reviewers.codex=2
cb scale coders.codex=2
cb scale reviewers.claude_sdk=2
```

After scaling:

```bash
cb setup-agents
cb
```

`cb scale` prints the new total agent count and warns if the config exceeds the free-tier 10-agent cap.

Scale coders and opposite-framework reviewers together for clean parallel review:

| Coder pool | Reviewer pool needed for cross-model review |
|------------|---------------------------------------------|
| `coders.claude_sdk=N` | `reviewers.codex>=N` |
| `coders.codex=N` | `reviewers.claude_sdk>=N` |

Multiple planners and plan reviewers are supported, but they are mainly a throughput feature for multiple queued tasks. For a single task, one Planner and one opposite-framework Plan Reviewer is usually the best default. If you scale them, keep the same pairing rule: `planners.claude_sdk=N` should have `plan_reviewers.codex>=N`, and `planners.codex=N` should have `plan_reviewers.claude_sdk>=N`.

## Review Guidelines

Add project-specific guidance at the pool level:

```yaml
reviewers:
  claude_sdk: { count: 1 }
  codex:      { count: 1 }
  review_guidelines: "All public functions need docstrings. No raw SQL."

plan_reviewers:
  claude_sdk: { count: 0 }
  codex:      { count: 1 }
  review_guidelines: "Reject plans that assign the same file to multiple coders."
```

## Merge Policy

The Code Reviewer assigns a risk level to every PR:

| Risk | Examples | Default behavior |
|------|----------|------------------|
| Low | Docs, tests, config, cosmetic fixes | Auto-merge |
| Medium | New features with tests, moderate logic | Human approval |
| High | Security-sensitive code, public API changes | Human approval |
| Critical | Auth, payments, deletion, infrastructure | Human approval |

Control auto-merge with:

```yaml
agents:
  mergemaster:
    auto_merge: "low"  # all | low | medium | none
```

## Merge Verdicts

For fresh installs, `agents.required_verdicts: null` resolves at task registration time to `["review"]`, plus `verify_acceptance` whenever a verifier is configured. The local `verify` verdict is opt-in: set `agents.handoff_verify_command` to make `cb-phase verify` run that command and require its passing verdict before review.

```yaml
agents:
  handoff_verify_command: "pytest"
```

With the default active verifier pool and no `handoff_verify_command`, tasks require `review` and `verify_acceptance`. With both verifier counts set to `0`, the default is just `review`. An explicit `required_verdicts` list is validated at task registration; `verify` requires `handoff_verify_command`, and `verify_acceptance` requires at least one configured verifier.

## Memory Backend

Codeband probes Band.ai memory at startup:

| Tier | Backend | Multi-host |
|------|---------|------------|
| Paid Band.ai | Band.ai memory REST API | Yes |
| Free Band.ai | Local JSONL at `workspace/state/memories.jsonl` | No |

Force a backend when debugging:

```bash
export BAND_MEMORY_MODE=local  # band | local | auto
```

or:

```yaml
band:
  memory_mode: local
```

## Reconnect & Room Subscription (local mode)

In local mode (plain `cb` / `cb run`), agents **rejoin existing rooms by
default** at startup and on every reconnect cycle — scoped to rooms tied to
an `active` task in the durable state store. This is the mid-task recovery
path: rejoin, then the SDK drains the room backlog through the agent's
rehydrated context. Rooms not tied to an active task are skipped (one INFO
line reports the skipped count), capping the blast radius of stale-room
backlog storms. If the state store is unreadable, the sweep logs one
ERROR-level line and subscribes to **all** participant rooms — it fails
toward connectivity, never toward deafness.

Opt out with:

```bash
cb run --fresh   # skip rejoining existing rooms and their backlog (fresh start)
```

`CODEBAND_LOCAL_SUBSCRIBE_EXISTING` (the old opt-in from when the default
was to skip) is deprecated and ignored; setting it prints one deprecation
warning.

The delivery backstop for missed websocket pushes is the SDK's idle resync —
how quickly an idle agent re-polls its pending queue — tuned via:

```yaml
agents:
  idle_resync_seconds: 30   # default; minimum 1
```

It applies to every role uniformly. Lower values recover faster from missed
pushes but generate more REST traffic (each resync fires one poll per
subscribed room).

Distributed mode (`cb run --agent <key>`) is intentionally untouched: it
runs the SDK-native reconnect and subscribe-existing behavior.

## Environment Variables

Recovery-critical variables that change where Codeband reads state or how it
authenticates. All are optional; defaults are correct for a standard install.

| Variable | What it does |
|----------|--------------|
| `WORKSPACE` | Base directory for resolving a **relative** `workspace.path`. When set, `workspace.path` resolves against `$WORKSPACE` instead of the project directory — the one shared rule (`config.resolve_workspace_path`) used by the runner, `cb-phase` / `cb approve`, task registration, and `cb doctor`. The Docker images set it to `/workspace` (the shared volume), so every container resolves state to the same place. Absolute `workspace.path` values ignore it. |
| `CODEBAND_PROJECT_DIR` | Project directory (config files + active-room pointer) used by `cb-phase` / `cb approve` to resolve context from any cwd, and by `cb up` / `cb down` for compose interpolation. The compose files set the in-container value to `/app/config`. |
| `WATCHDOG_LIVENESS_MODE` | Force the watchdog's liveness signal: `human` (richer human-API signal, enterprise-only) or `agent` (always-available agent-API inbox signal). Overrides `band.liveness_mode` and skips the startup probe. Invalid values are ignored with a warning. |
| `CODEBAND_CLAUDE_PREFER_API_KEY` | Set to `1`, `true`, `yes`, or `on` to keep `ANTHROPIC_API_KEY` active even when Claude subscription OAuth exists. This opts out of Codeband's default subscription-first Claude auth. |
| `CODEBAND_CODEX_PREFER_API_KEY` | Set to `1`, `true`, `yes`, or `on` to keep `OPENAI_API_KEY` active even when Codex ChatGPT subscription auth exists. This opts out of Codeband's default subscription-first Codex auth. |
| `CODEBAND_FALLBACK_ANTHROPIC_API_KEY` | Process-local backup of a stripped `ANTHROPIC_API_KEY`. Codeband strips the key at startup when Claude subscription OAuth exists (subscription-first policy); preflight restores it from this variable only after the subscription path reports usage-limit exhaustion. Set automatically — you only need to set it manually when providing a fallback key the environment never had. |
| `CODEBAND_FALLBACK_OPENAI_API_KEY` | Same mechanism for Codex: backup of a stripped `OPENAI_API_KEY` when a Codex ChatGPT subscription is logged in, restored by preflight on subscription usage-limit exhaustion or when Codex falls into an API-key-required auth mode after the strip. |

## Manual Agent Registration (Free Tier)

If `cb setup-agents` is unavailable, create these ten agents in the Band.ai web UI:

| Role | Recommended Band.ai name |
|------|--------------------------|
| Conductor | `Conductor` |
| Mergemaster | `Mergemaster` |
| Claude planner | `Planner-Claude-0` |
| Codex plan reviewer | `Plan-Reviewer-Codex-0` |
| Claude coder | `Coder-Claude-0` |
| Codex coder | `Coder-Codex-0` |
| Claude code reviewer | `Reviewer-Claude-0` |
| Codex code reviewer | `Reviewer-Codex-0` |
| Claude verifier | `Verifier-Claude-0` |
| Codex verifier | `Verifier-Codex-0` |

Then create `agent_config.yaml` next to `codeband.yaml`:

```yaml
agents:
  conductor:
    agent_id: <paste from Band.ai>
    api_key:  <paste from Band.ai>
  mergemaster:
    agent_id: ...
    api_key:  ...
  planner-claude_sdk-0:
    agent_id: ...
    api_key:  ...
  plan_reviewer-codex-0:
    agent_id: ...
    api_key:  ...
  coder-claude_sdk-0:
    agent_id: ...
    api_key:  ...
  coder-codex-0:
    agent_id: ...
    api_key:  ...
  reviewer-claude_sdk-0:
    agent_id: ...
    api_key:  ...
  reviewer-codex-0:
    agent_id: ...
    api_key:  ...
  verifier-claude_sdk-0:
    agent_id: ...
    api_key:  ...
  verifier-codex-0:
    agent_id: ...
    api_key:  ...
```

The keys on the left are load-bearing. They must match the configured role, framework, and zero-based index exactly.
