# IO Coding Agent — Milestone 1

An autonomous coding agent that runs on the existing InfoOrigin AI stack
(Open WebUI → LiteLLM → Ollama/Qwen). It inspects a real Git repository,
creates an isolated workspace, makes real line-numbered code edits, runs the
real test suite in a disposable Docker container, retries a bounded number of
times, and reports a verified `git diff` through an OpenAI-compatible API.

Milestone 1 is deliberately narrow. It is a real, working coding loop, not a
claim that every DevOps integration already exists. Docker build, Kubernetes
and the InfoOrigin integrations are explicit `disabled` / `not_configured`
stubs; they never fake success.

---

## 1. What works in Milestone 1

| Capability | Status |
|---|---|
| Repository inspection (tree, languages, manifests, tests) | Working |
| Isolated workspace per task, dedicated `io-agent/<id>` branch | Working |
| Real code edits with line-numbered replacements | Working |
| Real test execution in an unprivileged, network-off container | Working |
| Bounded diagnosis/retry loop | Working |
| Verified Git diff and honest final report | Working |
| OpenAI-compatible SSE endpoint for Open WebUI | Working |
| Structured task/event/tool/approval/trace persistence | Working |
| Human approval for protected files and deletes | Working |
| No automatic `git push`; no remote write tools | Enforced |
| Docker build / Kubernetes / InfoOrigin integrations | Disabled stubs |
| Multi-user RBAC, remote GitLab API, auto-publish | Not in Milestone 1 |

---

## 2. Architecture

```text
┌──────────────────┐     OpenAI-compatible SSE      ┌──────────────────────────┐
│   Open WebUI     │ ─────────────────────────────► │  io-agent (FastAPI)      │
│  model:          │   http://io-agent:8080/v1      │  LangGraph workflow      │
│  io-coding-agent │ ◄───────────────────────────── │  SQLAlchemy → PostgreSQL │
└──────────────────┘                                └───────────┬──────────────┘
                                                                │ HTTP + token
                                                                ▼
                                                    ┌──────────────────────────┐
                                                    │ sandbox-runner           │
                                                    │ only holder of           │
                                                    │ /var/run/docker.sock     │
                                                    └───────────┬──────────────┘
                                                                │ docker run
                                                                ▼
                                                    ┌──────────────────────────┐
                                                    │ disposable task container│
                                                    │ no network, no caps,     │
                                                    │ read-only root, 1 vCPU,  │
                                                    │ 768 MB, user 10001       │
                                                    └──────────────────────────┘

Existing services reused unchanged:
  litellm (http://litellm:4000/v1), litellm_postgres (PostgreSQL 16),
  open-webui (127.0.0.1:8080), omniroute, Ollama (host).
  Docker network: litellm_ai-network (created by the existing Compose project).
```

### Workflow nodes

1. **understand** — extract repository URL, create/reuse the workspace, create
   the dedicated branch.
2. **inspect** — deterministic tree/language/manifest analysis plus a bounded
   model-driven read/search loop.
3. **plan** — constrained JSON plan and file selection.
4. **edit** — constrained JSON line edits (`start_line`, `end_line`,
   `replacement`), syntax validation, rollback on failure.
5. **test** — run the detected test command in the sandbox.
6. **diagnose** — bounded correction attempts with the real failure output.
7. **verify** — capture `git status`, `git diff` and `git diff --stat`.
8. **finalize** — save an honest summary, trace, and terminal event.

The model is not trusted to emit native tool calls. Probes showed that
`qwen2.5-coder:1.5b` returns fenced JSON in `content` and sometimes echoes
schema placeholders. The agent therefore validates every response with
Pydantic and repairs invalid responses a bounded number of times.

---

## 3. Security model

- **No Docker socket in the agent.** Only `sandbox-runner` mounts
  `/var/run/docker.sock`. The agent calls a narrow, token-authenticated API.
- **No Docker socket in task containers.** Task containers are created with
  `--cap-drop ALL`, `no-new-privileges`, read-only root filesystem, a tmpfs
  `/tmp`, `--network none` by default, PID/memory/CPU limits, and user
  `10001:10001`.
- **Workspace isolation.** A task container mounts exactly one workspace
  subdirectory, read-write, at `/workspace`. Path traversal and `.git` access
  through file tools are rejected.
- **Command policy.** Commands are parsed into argv (never a shell). Only an
  allowlist of build/test/inspection binaries is accepted. Shell operators,
  `sudo`, `rm`, `curl`, `git push`, `git reset --hard`, Docker/Kubernetes
  clients, and similar operations are denied. The runner applies a second
  validation layer.
- **Protected paths.** `.env`, keys, Dockerfiles, Compose files, CI/CD,
  Kubernetes, Terraform, migrations, and secrets require human approval before
  an edit or delete.
- **Test protection.** For "fix the failing test" tasks, editing test files is
  refused. The agent must change the implementation.
- **No remote writes.** There is no push tool. The command policy denies
  `git push`. Work happens on a dedicated local branch.
- **Secrets.** `.env` is git-ignored. The agent uses a dedicated LiteLLM
  virtual key, not the master key. The sandbox token and API key are random.
- **Network exposure.** The agent binds to `127.0.0.1:8091` on the VM. Open
  WebUI reaches it over the private Docker network. No internal service is
  published publicly.

---

## 4. Repository layout

```text
io-agent/
├── agent/
│   ├── config.py                 # all settings, model never hardcoded
│   ├── llm.py                    # LiteLLM OpenAI-compatible client
│   ├── protocol.py               # JSON extraction, Pydantic validation, repair
│   ├── graph/builder.py          # LangGraph workflow and routing
│   ├── nodes/                    # understand, inspect, plan, edit, test, ...
│   ├── orchestration/runner.py   # task lifecycle, persistence, approval resume
│   └── prompts/system.py         # constrained prompts
├── api/
│   ├── main.py                   # FastAPI app/lifespan
│   ├── deps.py                   # API key auth and service lookup
│   ├── routes/                   # chat, tasks, approvals, models, capabilities
│   └── schemas/openai.py         # OpenAI-compatible schemas
├── execution/
│   ├── policies/                 # command and path policy
│   ├── sandbox/client.py         # client for sandbox-runner
│   └── workspace/manager.py      # clone, confinement, test detection, git
├── persistence/                  # SQLAlchemy models and repository
├── telemetry/                    # event bus and sanitization
├── tools/                        # filesystem, git, terminal, docker, k8s, integrations
├── sandbox/
│   ├── Dockerfile                # ephemeral task image
│   ├── runner.Dockerfile         # runner image (Docker socket)
│   └── runner.py                 # narrow authenticated execution API
├── scripts/
│   ├── deploy.sh                 # one-shot deployment
│   ├── bootstrap_db.sh           # dedicated io_agent database
│   ├── create_litellm_key.sh     # scoped LiteLLM virtual key
│   ├── install_demo_repo.sh      # local fixture repository
│   ├── register_openwebui_connection.sh
│   ├── register_openwebui_connection.py
│   ├── run_demo.sh               # end-to-end demo
│   └── probe_model.py            # model protocol capability check
├── dataset_export/export.py      # approved-trace JSONL export
├── tests/                        # unit + end-to-end fake-LLM tests
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
└── .env.example
```

---

## 5. Prerequisites

On the existing VM:

- Docker 29+ with Compose v2 (`docker compose`).
- The existing LiteLLM stack is healthy: `litellm`, `litellm_postgres`,
  `open-webui`, `omniroute`.
- The Docker network `litellm_ai-network` exists.
- LiteLLM serves the configured model alias (default
  `qwen2.5-coder:1.5b`).
- The Open WebUI container is named `open-webui` and uses
  `/app/backend/data/webui.db`.
- `sudo -n docker ...` works for the deployment user, or run the scripts as
  root.
- `/srv/io-agent` can be created (about 1–2 GB for workspaces and images).

Check:

```bash
sudo docker network ls | grep litellm_ai-network
sudo docker ps --format '{{.Names}} {{.Status}}'
curl -s http://127.0.0.1:4000/v1/models \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | head
```

---

## 6. Deployment (Linux)

The project can live anywhere; the example uses `~/io-agent`. It does **not**
modify the existing `~/Io-agent/litellm` Compose project.

```bash
# 1. Copy the project to the VM (from your workstation).
scp -r io-agent ubuntu@172.30.30.154:~/

# 2. On the VM:
cd ~/io-agent
chmod +x scripts/*.sh
./scripts/deploy.sh
```

`deploy.sh` performs the following idempotent steps:

1. Creates `.env` from `.env.example` if missing, with `0600` permissions.
2. Generates `AGENT_API_KEY`, `SANDBOX_TOKEN`, and a database password when
   they are still template values.
3. Creates `/srv/io-agent/{workspaces,repos,exports}` and sets ownership
   `10001:10001` on the writable directories.
4. Creates the `io_agent` role and database in the existing PostgreSQL server.
5. Creates a scoped LiteLLM virtual key for `AGENT_MODEL` and stores it in
   `.env` (never prints it).
6. Creates the local demo fixture repository.
7. Builds and starts the separate `io-agent` Compose project.
8. Waits for health and registers the agent as an Open WebUI connection.

Useful manual commands:

```bash
# Build without starting
sudo docker compose build

# Start / stop / status / logs
sudo docker compose up -d
sudo docker compose down
sudo docker compose ps
sudo docker compose logs -f io-agent sandbox-runner

# Rebuild only the agent
sudo docker compose up -d --build io-agent
```

The deployment does not add the agent to the public internet. The only host
port is `127.0.0.1:8091`.

---

## 7. Environment variables

Copy `.env.example` to `.env`. The most important values:

| Variable | Purpose |
|---|---|
| `AGENT_MODEL` | LiteLLM model alias. Change this to swap models; never hardcoded. |
| `AGENT_PUBLIC_MODEL_ID` | Model id shown to Open WebUI (default `io-coding-agent`). |
| `LITELLM_BASE_URL` | LiteLLM base URL (default `http://litellm:4000/v1`). |
| `LITELLM_API_KEY` | Dedicated LiteLLM virtual key. **Not** the master key. |
| `AGENT_API_KEY` | Bearer token for the agent API. Shared with Open WebUI. |
| `SANDBOX_TOKEN` | Shared secret between agent and sandbox runner. |
| `AGENT_DATABASE_URL` | Dedicated `io_agent` database URL. |
| `IO_AGENT_HOST_ROOT` | Host root for workspaces/repos/exports (default `/srv/io-agent`). |
| `IO_AGENT_PORT` | Loopback API port (default `8091`). |
| `AGENT_PROTECT_TESTS` | Refuse test edits for "fix the failing test" tasks. |
| `AGENT_MAX_ATTEMPTS` | Bounded diagnosis attempts (default `3`). |
| `AGENT_TASK_TIMEOUT_SECONDS` | Hard task limit (default `1800`). |
| `AGENT_REPO_ALLOWLIST` | Optional comma-separated HTTPS host allowlist. |
| `AGENT_ENABLE_DOCKER_BUILD` | Milestone 1 default `false`; returns a disabled stub. |
| `AGENT_ENABLE_KUBERNETES` | Milestone 1 default `false`; returns a disabled stub. |

Generate secrets manually if you prefer:

```bash
openssl rand -hex 32   # AGENT_API_KEY
openssl rand -hex 32   # SANDBOX_TOKEN
openssl rand -hex 24   # IO_AGENT_DB_PASSWORD
```

To use a different model, change `AGENT_MODEL` and recreate the scoped key:

```bash
FORCE=1 ./scripts/create_litellm_key.sh
sudo docker compose up -d --force-recreate io-agent
```

The model must exist in the existing `litellm/config.yml` (or be added there).
The agent does not edit that file.

---

## 8. API

All endpoints except `/health` require:

```http
Authorization: Bearer <AGENT_API_KEY>
```

### `GET /v1/models`

OpenAI-compatible model list containing `AGENT_PUBLIC_MODEL_ID`.

### `POST /v1/chat/completions`

OpenAI-compatible endpoint used by Open WebUI. Supports `stream: true` (SSE)
and non-streaming responses.

```bash
curl -N http://127.0.0.1:8091/v1/chat/completions \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "io-coding-agent",
    "stream": true,
    "messages": [{
      "role": "user",
      "content": "Fix the failing test and show me the diff.\nrepo: fixture://demo-bugfix"
    }]
  }'
```

The repository can be supplied in the message (`repo: <url>`), via
`metadata.agent.repository`, or via `metadata.repository`. HTTPS Git URLs are
supported; local demo repositories use `fixture://<name>`.

### `POST /v1/tasks`

Create and start a structured task.

```bash
curl http://127.0.0.1:8091/v1/tasks \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "request": "Fix the failing test and show me the diff.",
    "repository": "fixture://demo-bugfix",
    "user": "demo"
  }'
```

Response: `{"task_id": "...", "status": "started", "events_url": "..."}`.

### `GET /v1/tasks`

List recent tasks.

### `GET /v1/tasks/{task_id}`

Task status, plan, tests, diff stat, summary, pending approvals, and tool calls.

### `GET /v1/tasks/{task_id}/events`

Server-Sent Events stream of structured task events. Honours `since` and
`follow` query parameters.

### `GET /v1/tasks/{task_id}/diff`

Verified `git status`, `git diff`, and `git diff --stat` for the workspace.

### `GET /v1/approvals`

List pending approvals. `POST /v1/approvals/{id}/decide` with
`{"decision":"approve|reject","approver":"name"}` resumes or rejects the task.

### `GET /v1/capabilities`

Model, tool list, phase boundaries, integration configuration status, and
Milestone 1 feature flags.

### `GET /v1/tools`

Tool schemas registered in the registry.

### `GET /health`, `GET /ready`

Liveness and dependency readiness (database, sandbox, LiteLLM, workspaces).

---

## 9. Open WebUI integration

The deployment script registers the connection automatically. It is the
scripted equivalent of **Settings → Connections → Add Connection**:

- Base URL: `http://io-agent:8080/v1`
- API key: the `AGENT_API_KEY` from `.env`
- Model: `io-coding-agent`

The script:

1. Backs up `/app/backend/data/webui.db` inside the container.
2. Appends `http://io-agent:8080/v1` to `openai.api_base_urls`.
3. Appends the agent key to `openai.api_keys`.
4. Adds the matching `openai.api_configs` entry with
   `model_ids: ["io-coding-agent"]`.
5. Restarts Open WebUI.

To run it again:

```bash
./scripts/register_openwebui_connection.sh
```

Manual alternative: in Open WebUI, open **Settings → Connections**, add an
OpenAI connection with the URL and key above, then save. The agent's model will
appear in the model selector. Existing LiteLLM/Ollama connections are
preserved.

In chat, include the repository in the message:

```text
Fix the failing test and show me the verified diff.
repo: https://iogitlab.infoorigin.com/group/project.git
```

For a task that adds tests rather than fixing them, say "add tests" so the test
protection rule allows test-file edits.

---

## 10. Demo

Create the fixture (the deploy script does this automatically):

```bash
./scripts/install_demo_repo.sh
```

Fixture contents:

```python
# app/calculator.py
def add(a, b):
    return a - b
```

```python
# tests/test_calculator.py
from app.calculator import add


def test_add():
    assert add(2, 3) == 5
```

Run the demo:

```bash
./scripts/run_demo.sh
```

Equivalent API call:

```bash
curl http://127.0.0.1:8091/v1/tasks \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "request": "Fix the failing test in the repository and show me the verified diff.\nrepo: fixture://demo-bugfix",
    "repository": "fixture://demo-bugfix"
  }'
```

Expected verified diff:

```diff
diff --git a/app/calculator.py b/app/calculator.py
index ...
--- a/app/calculator.py
+++ b/app/calculator.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
```

The exact index hash and line context depend on the Git version. The demo
script prints the real diff and the stored summary.

---

## 11. Tests

Run the unit and end-to-end workflow tests in the test image:

```bash
sudo docker build --target test -t io-agent-test .
sudo docker run --rm io-agent-test
```

Or locally with a Python 3.12 virtual environment:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

The end-to-end test uses a deterministic fake model and a fake sandbox that
runs commands locally. It verifies the real graph, real line edits, real
pytest execution, real Git diff capture, persistence, and the protected-path
approval path. It does not need Docker or network access.

Probe the actual model protocol through LiteLLM:

```bash
sudo docker compose exec io-agent python scripts/probe_model.py
```

---

## 12. Dataset export

Only traces explicitly approved for training are exported:

```bash
sudo docker compose exec io-agent python -m dataset_export.export \
  --output /exports/approved_traces.jsonl
```

The JSONL records contain sanitized messages, the verified diff, test outcome,
model, branch, and human feedback. No credentials are included. Review before
using the data for any fine-tuning.

---

## 13. Operations

```bash
# Status
sudo docker compose ps
curl -s http://127.0.0.1:8091/ready | python3 -m json.tool

# Logs
sudo docker compose logs -f --tail=200 io-agent
sudo docker compose logs -f --tail=200 sandbox-runner

# Restart after an env change
sudo docker compose up -d --force-recreate io-agent

# Stop the agent (does not touch LiteLLM/Open WebUI)
sudo docker compose down

# Remove old workspaces (destructive; review first)
sudo find /srv/io-agent/workspaces -mindepth 1 -maxdepth 1 -type d -mtime +7 -print
```

Database backup:

```bash
sudo docker exec litellm_postgres pg_dump -U litellm -d io_agent \
  | gzip > io_agent-$(date +%F).sql.gz
```

Key rotation:

```bash
FORCE=1 ./scripts/create_litellm_key.sh
sudo docker compose up -d --force-recreate io-agent
./scripts/register_openwebui_connection.sh
```

---

## 14. Known limitations (Milestone 1)

- Docker build, Kubernetes, ioProjects, ioGitLab, ioAgrco, ioArtifactory and
  ioDB adapters are explicit stubs. They return `disabled` or
  `not_configured`; they do not make network calls or invent API paths.
- No automatic commit or push. The verified diff is available through the API;
  publishing is a separate human decision.
- No multi-tenant RBAC. The API uses one shared bearer key. Open WebUI OAuth
  identities can be forwarded only when `AGENT_TRUST_FORWARDED_USER=true` and a
  trusted proxy strips client-supplied headers.
- The agent's quality depends on the configured model. The constrained JSON
  protocol and deterministic inspection safety net reduce, but do not
  eliminate, model errors. Bounded retries prevent infinite loops.
- Test execution uses the sandbox image's toolchain. Repositories that need
  additional system packages or a different runtime need a custom task image.
- HTTPS cloning supports public repositories or URLs with embedded
  credentials. A proper Git credential helper/SSH agent is not part of
  Milestone 1; do not put long-lived tokens in repository URLs.
- Workspace retention is manual. There is no automatic garbage collector.
- The demo fixture is local. Remote repositories must be reachable from the
  `io-agent` container and allowed by `AGENT_REPO_ALLOWLIST` if set.

---

## 15. Troubleshooting

| Symptom | Check |
|---|---|
| `/ready` reports `sandbox: false` | `docker compose logs sandbox-runner`; confirm `/var/run/docker.sock` is mounted and the `io-agent-sandbox:latest` image exists. |
| `/ready` reports `litellm: false` | Confirm `LITELLM_API_KEY` is valid and LiteLLM is on `litellm_ai-network`. |
| Task stops at `needs_clarification` | Include a `repo:` line or `repository` field. |
| Model returns invalid JSON repeatedly | Run `scripts/probe_model.py`; try a stronger model via `AGENT_MODEL`. |
| `git clone` fails for HTTPS | Check DNS/egress from the agent container and the repository allowlist. |
| Open WebUI does not show the model | Re-run `register_openwebui_connection.sh`; check `docker logs open-webui` and the connection key. |
| Tests fail with missing tools | The default sandbox image contains Python, pytest, git, and common build/test tools. Add packages to `sandbox/Dockerfile` for other stacks. |
| Disk is full | `sudo docker system df`; prune unused build cache/images after reviewing. |

---

## 16. Non-goals and guarantees

- The agent **never** claims a test passed unless the sandbox returned exit code
  0 for the recorded command.
- The agent **never** claims a file changed unless the edit was written and the
  final Git diff is non-empty.
- The agent **never** pushes to a remote.
- The agent **never** receives the Docker socket.
- The agent **never** commits `.env`, keys, tokens, or credentials.
