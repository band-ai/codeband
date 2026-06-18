# Authentication

Codeband coordinates Band.ai agents and shells out to Claude Code, Codex, `git`, and `gh`. This page explains which credentials are used and in what order.

## Required Credentials

At minimum, a full cross-model run needs:

```ini
BAND_API_KEY=band_u_...
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

`GH_TOKEN` is recommended for Docker and CI because Codeband uses GitHub PR and issue workflows through `gh`.

## Claude

Claude agents can authenticate in three ways. Codeband resolves them in this order at `cb run` startup:

1. `CLAUDE_CODE_OAUTH_TOKEN`, from `claude setup-token`. Recommended for Docker and CI.
2. Host subscription OAuth:
   - macOS: Keychain entry written by `claude` login.
   - Linux/Windows: `$CLAUDE_CONFIG_DIR/.credentials.json`, defaulting to `~/.claude/.credentials.json`.
3. `ANTHROPIC_API_KEY`, for pay-per-token usage.

When subscription auth is available, Codeband strips `ANTHROPIC_API_KEY` from the spawned Claude process so the Claude CLI does not silently prefer API-key billing over subscription auth. `cb doctor` warns when both are present.

Set `CODEBAND_CLAUDE_PREFER_API_KEY=1` to keep API-key precedence when both auth methods are present.

## Codex

Codex agents can authenticate in two ways. Codeband resolves them in this order at `cb run` startup:

1. Host ChatGPT subscription login from `codex login --device-auth`, stored in `~/.codex/auth.json`.
2. `OPENAI_API_KEY`, for pay-per-token usage.

When ChatGPT subscription auth is available, Codeband strips `OPENAI_API_KEY` from the spawned Codex process so the Codex CLI does not silently prefer API-key billing over subscription auth. Preflight restores the stripped key if the subscription path reports usage-limit exhaustion or if Codex falls into an API-key-required auth mode. `cb doctor` warns when both are present.

Set `CODEBAND_CODEX_PREFER_API_KEY=1` to keep API-key precedence when both auth methods are present.

For Docker, mount or provide credentials explicitly:

- Set `OPENAI_API_KEY` in `.env`, or
- Bind-mount `~/.codex/auth.json` into the container environment.

## Band.ai

`BAND_API_KEY` is required for task submission, agent setup, WebSocket communication, and memory backend detection.

Paid/enterprise Band.ai accounts can use:

```bash
cb setup-agents
```

Free-tier accounts may need manual agent creation. See [Configuration](CONFIGURATION.md#manual-agent-registration-free-tier).

## GitHub

Codeband uses `gh` for PR and issue workflows.

Local development can use an interactive `gh auth login`. For Docker and CI, set:

```ini
GH_TOKEN=ghp_...
```

Use a token with the minimum repository permissions needed for the target workflow.

## Preflight

`cb run` makes a tiny call through each configured framework CLI before spawning agents. This catches billing, login, quota, and rate-limit failures at startup instead of letting the swarm stall later.

Skip preflight only when you intentionally need to run offline or in a constrained CI job:

```bash
cb run --skip-preflight
```

## Docker Caveat

Containers cannot read the host macOS Keychain. For Docker mode, put one of these in `.env`:

```ini
CLAUDE_CODE_OAUTH_TOKEN=...
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GH_TOKEN=...
```

`cb up` forwards `.env` into the containers.
