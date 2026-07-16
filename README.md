# Ghosty Agents — GCP agent fleet

Spin up and manage a fleet of hardened, IAP-only agent VMs on a dedicated Google
Cloud project with its own isolated billing account.

There are two ways to use this repo:

1. **`ghosty-agents` CLI (recommended)** — a cross-platform (Windows/Linux/Mac)
   Python CLI that manages multiple agents, stores/validates config, and includes
   an interactive control room plus scriptable commands. See [`docs/cli.md`](docs/cli.md).
2. **Bash scripts** — the original step-by-step scripts the CLI is built on
   (Mac/Linux). Useful for learning or one-off runs. See below.

## CLI quick start

Requires Python 3.11+ and the [`gcloud` CLI](https://cloud.google.com/sdk/docs/install)
(`gcloud auth login`).

### Install from GitHub

For a released version, install the wheel from the GitHub Release with
[`pipx`](https://pipx.pypa.io/). Replace only the version with the value shown
on the [release page](https://github.com/castri1/ghosty-agents/releases):

```bash
pipx install https://github.com/castri1/ghosty-agents/releases/download/v0.1.0/ghosty_agents-0.1.0-py3-none-any.whl
```

Alternatively, install directly from a Git tag:

```bash
pipx install "git+https://github.com/castri1/ghosty-agents.git@v0.1.0"
```

See [`docs/github-distribution.md`](docs/github-distribution.md) to publish a
GitHub release.

### Install for development

```bash
python3 -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

ghosty-agents               # open the interactive control room
ghosty-agents init          # store project / billing / account
ghosty-agents check         # verify local and cloud setup
ghosty-agents prepare       # one-time shared project preparation
ghosty-agents internet enable # optional: shared outbound internet for private agents
ghosty-agents models enable # optional: let agents call Google AI models
ghosty-agents storage add   # optional: private per-agent file storage
ghosty-agents create worker-1 --with-hermes # create an agent and install Hermes
ghosty-agents hermes status worker-1 # check Hermes install/config/gateway state
ghosty-agents chat add worker-1 --chat-project ghosty-agent-chat # optional: Google Chat
ghosty-agents notifications add worker-1 --with-consumer # optional: external events into Hermes
ghosty-agents instruct worker-1 --service chat # optional: brief Hermes over SSH/IAP
ghosty-agents agents        # inventory
ghosty-agents connect worker-1 # connect to the agent
ghosty-agents remove worker-1 # remove one agent
```

Full command reference: [`docs/cli.md`](docs/cli.md).

## Design

- **Multi-agent model:** one dedicated project; each agent is one hardened VM
  sharing the VPC/subnet/IAP-firewall/budget created once by `bootstrap`. Each
  agent gets its own least-privilege service account. Optional shared Cloud NAT
  can provide outbound internet without adding external IPs to VMs.
- **Inventory source of truth is GCP:** agents are labelled
  `managed-by=ghosty-agents` + `ghosty-agent=<name>`; `list` queries live so local
  state can't drift.
- **Hardening per VM:** no external IP, Shielded VM, OS Login, IAP-only SSH.
- **Outbound internet:** disabled by default except for Google APIs via Private
  Google Access. Use `ghosty-agents prepare --with-nat` or
  `ghosty-agents internet enable` if agents need general internet egress.
- **Google AI access:** optional. Use `ghosty-agents models enable` to enable
  the Agent Platform API (`aiplatform.googleapis.com`) and grant Ghosty agent
  service accounts `roles/aiplatform.user`.
- **Shared storage:** optional. Use `ghosty-agents storage add` to create a
  locked private bucket with one managed folder per agent. Use
  `--with-public` for a separate public bucket with per-agent public folders,
  and `--with-signed-urls` so agents can generate temporary links for private
  objects. Future agents inherit storage sync while enabled.
- **Hermes runtime:** optional but first-class. Use
  `ghosty-agents hermes install <agent>` to run the official Nous installer on
  the VM in a safer two-step download/run flow, then
  `ghosty-agents hermes configure <agent>` to point Hermes at Google models via
  Vertex AI using the VM service account. `ghosty-agents create <agent>
  --with-hermes` does this after VM creation. By default it tracks the vendor
  `main` installer; use `--commit` when you need a temporary pin.
- **Google Chat:** optional. Google Chat requires the Chat app and
  Pub/Sub topic to live in the same GCP project. Use
  `ghosty-agents chat add <agent> --chat-project <chat-project>` for an
  existing Chat app project, or omit `--chat-project` to let the CLI derive and
  create one. The CLI creates the Pub/Sub topic/subscription, service account,
  JSON key, and IAM bindings Hermes needs, uploads the key to the VM, and prints
  the exact Chat Console and Hermes values. The Google Chat API Console
  configuration still happens manually.
- **External notifications:** optional. Use `ghosty-agents notifications add <agent>` for
  a guided setup that asks what to call the notification path and how to set its secret.
  The CLI creates a
  public Cloud Run receiver that validates incoming webhook requests, publishes
  normalized events to Pub/Sub, grants the private VM subscription access, and
  writes `~/.config/hermes/webhooks/<name>.env` on the VM. No inbound VM
  firewall rules or external VM IPs are added. Source deployments also grant
  Cloud Run Builder to the project build service account when needed. Add
  `--with-consumer` to install a managed VM-side service that pulls events,
  saves them under `~/.config/hermes/inbox/events/<name>/`, acknowledges after
  the write, and invokes Hermes with a bounded prompt.
- **Agent removal:** `ghosty-agents remove <agent>` removes the VM, agent
  service account, per-agent notification gateways, per-agent storage
  IAM/managed folders, model IAM, and saved per-agent config mappings. Shared
  fleet resources stay in place while other agents still exist.
- **Agent briefings:** after attaching Chat, Notifications, Storage, or Models,
  use `--brief-agent` or `ghosty-agents instruct <agent> --service <service>` to
  upload a generated prompt to `~/.config/hermes/inbox` and run the configured
  Hermes command over SSH/IAP with a timeout. Override the command with
  `ghosty-agents settings set agent_instruction_command '"$HOME/.local/bin/hermes" -z "$(cat "$GHOSTY_PROMPT_FILE")"'`.
  The default timeout is 600 seconds; override it with
  `ghosty-agents settings set agent_instruction_timeout_seconds 900`.
- **Compatibility aliases:** the older technical commands (`bootstrap`, `ssh`,
  `google-chat`, `webhook`, `bucket`, `nat`, etc.) still work for existing scripts.
- **Harness view:** inside the interactive control room, select an agent and
  choose **View harness** to open a local browser diagram of its attached
  capabilities. The view is read-only, animated, and served only on
  `127.0.0.1`.
- **Isolation:** every `gcloud` call is pinned to a dedicated configuration plus
  explicit `--account`/`--project`, so a concurrent `gcloud` session in another
  project is never disturbed (CLI ports the `gc()` wrapper from `scripts/lib.sh`).

## Layout

```
.
├── pyproject.toml          # ghosty-agents CLI package (entrypoint: ghosty-agents)
├── ghosty/                 # CLI source
│   ├── cli.py              #   Typer commands
│   ├── config.py           #   config.toml load/save (replaces env.sh)
│   ├── gcloud.py           #   gcloud subprocess wrapper (isolation/idempotency)
│   ├── bootstrap.py        #   project/billing/network/iam/budget
│   ├── agents.py           #   up/list/status/ssh/start/stop/down
│   ├── doctor.py           #   readiness checks
│   └── models.py           #   Config, Agent, naming/labels
├── tests/                  # unit tests (gcloud layer mocked)
├── docs/
│   ├── cli.md              # CLI reference
│   ├── step-0-billing-and-project.md   # billing + project background
│   └── runbook-deployment.md           # hardened VM + IAP + budget background
├── env.example.sh / env.sh # config for the bash-script path (git-ignored real one)
└── scripts/                # original bash scripts (bootstrap/ + deploy/)
```

## Bash-script path (alternative)

The original scripts remain for Mac/Linux. They use `env.sh` for config and the
same isolated-config approach (`CLOUDSDK_ACTIVE_CONFIG_NAME`).

```bash
cp env.example.sh env.sh    # edit BILLING_ACCOUNT_ID, PROJECT_ID, MY_ACCOUNT
source env.sh
./scripts/bootstrap/setup-config.sh
./scripts/bootstrap/00-create-project.sh
./scripts/bootstrap/01-link-billing.sh
./scripts/bootstrap/02-verify-billing.sh
./scripts/deploy/deploy-all.sh     # APIs, SA, VPC, IAP firewall, IAM, VM, budget
./scripts/deploy/ssh-agent.sh
```

See [`docs/step-0-billing-and-project.md`](docs/step-0-billing-and-project.md) and
[`docs/runbook-deployment.md`](docs/runbook-deployment.md) for the background on
each step. Note: the bash scripts deploy a single VM, while the CLI manages a
multi-agent fleet. The bash scripts also omit Cloud NAT; use the Python CLI's
`nat` commands or add NAT manually if those VMs need general outbound internet.

## Prerequisites

- A Google Cloud account and the [`gcloud` CLI](https://cloud.google.com/sdk/docs/install),
  authenticated via `gcloud auth login`.
- A dedicated billing account (see `docs/step-0-billing-and-project.md`).
- For the CLI: Python 3.11+.

## Tests

```bash
pip install -e ".[dev]"
pytest
```
