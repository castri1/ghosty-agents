# Ghosty Agents technical reference

This document covers the architecture, security model, optional services,
resource lifecycle, legacy Bash workflow, and contributor setup. For the
interactive beginner workflow, start with the [README](../README.md). For
scriptable commands, use the [CLI reference](cli.md).

## Architecture and resource model

Ghosty manages a fleet in one dedicated Google Cloud project. Each agent is a
separate Compute Engine VM, while the fleet shares project-level resources such
as the VPC, subnet, IAP firewall rule, budget, and optional Cloud NAT gateway.
Each VM receives its own least-privilege service account.

Google Cloud is the inventory source of truth. Managed VMs are labelled with
`managed-by=ghosty-agents` and `ghosty-agent=<name>`, and the CLI queries the
live inventory instead of maintaining a separate local list.

The first billable resource is normally an agent VM. Enabling shared internet
while preparing the project can create a billable Cloud NAT resource before the
first agent exists. Model calls, storage, Chat, and notifications can introduce
additional usage charges.

## Security model

- Agent VMs have no external IP addresses.
- SSH access uses Identity-Aware Proxy and OS Login.
- Shielded VM features are enabled.
- Each agent uses a dedicated service account with scoped permissions.
- Private Google Access lets VMs reach Google APIs without public VM addresses.
- General outbound internet is optional and uses shared Cloud NAT; it does not
  permit inbound public connections to the VMs.
- Private storage uses uniform bucket-level access and public access prevention.
- Notification receivers terminate on Cloud Run and forward validated messages
  through Pub/Sub; they do not open inbound VM firewall rules.

See the [hardened deployment runbook](runbook-deployment.md) for the underlying
network and IAM design.

## Configuration and isolation

The CLI stores its configuration in the platform application directory returned
by `typer.get_app_dir("ghosty-agents")`, typically
`~/.config/ghosty-agents/config.toml` on Linux and macOS. Set
`GHOSTY_CONFIG_DIR` to override the location.

Every `gcloud` call uses a dedicated `ghosty-agents` configuration plus explicit
account and project arguments. This keeps Ghosty isolated from other concurrent
Google Cloud CLI sessions.

The saved configuration contains fleet defaults and mappings for optional
services. Agent inventory itself remains live in Google Cloud.

## Shared and per-agent capabilities

### Internet

Outbound internet is disabled by default except for Google APIs through Private
Google Access. `ghosty-agents internet enable` creates a Cloud Router and Cloud
NAT shared by private Ghosty VMs in the configured region and subnet. Use
`ghosty-agents internet disable` to remove general egress.

Hermes installation downloads vendor software, so the guided setup recommends
shared internet when Hermes is selected.

### Google AI models

`ghosty-agents models enable` enables the Vertex AI API, grants existing agent
service accounts `roles/aiplatform.user`, and records that future agents should
receive the same role. Disabling models removes that access from current agents
and turns off future automatic grants.

### Storage

`ghosty-agents storage add` creates a private bucket, with one managed folder at
`agents/<agent>/` per agent. Each agent service account receives
`roles/storage.objectUser` only for its own folder. The CLI writes the resulting
values to `~/.config/hermes/storage.env` on running VMs.

Optional public publishing uses a separate public bucket with per-agent managed
folders. Optional signed-link support grants the agent self-signing permission
for temporary private-object URLs. `storage disable` removes access and Hermes
configuration but preserves buckets, folders, and objects.

### Hermes

Hermes is optional but integrated into the guided agent setup. Installation uses
the official Nous installer, followed by the Hermes gateway installation.
Configuration selects the Vertex provider and relies on the VM service account
instead of an API key.

The default model is `google/gemini-3.1-pro-preview` in the Vertex `global`
region. Use `ghosty-agents hermes sync <agent>` to install or update Hermes and
apply the model configuration together.

### Google Chat

Google Chat requires the Chat app and Pub/Sub topic to be in the same Google
Cloud project. Ghosty can reuse an existing Chat project or derive and create a
project for the agent. It provisions the Pub/Sub topic and subscription, a
gateway service account, IAM bindings, and a service-account key uploaded to the
VM.

The Google Chat API Console configuration remains manual. After provisioning,
Ghosty prints the exact topic, project, subscription, and key-path values to use.

### External notifications

Notifications create a public Cloud Run receiver, Pub/Sub topic and subscription,
publisher and subscriber IAM, and a Hermes environment file under
`~/.config/hermes/webhooks/<name>.env`. Incoming systems authenticate with the
secret in `X-Ghosty-Webhook-Secret`.

The optional VM-side consumer pulls events with the agent service account,
writes them under `~/.config/hermes/inbox/events/<name>/`, acknowledges only
after the durable write, and then invokes Hermes with a bounded prompt. Cloud NAT
is not required for Pub/Sub delivery.

### Agent briefings

After models, storage, Chat, or notifications are attached, Ghosty can upload a
generated briefing prompt and invoke Hermes over SSH/IAP. The default behavior
asks interactively. It is controlled by `agent_instruction_delivery`,
`agent_instruction_command`, and `agent_instruction_timeout_seconds` in the
saved configuration.

## Resource lifecycle

Stopping an agent preserves the VM and attached resources while avoiding normal
VM runtime charges. Removing an agent deletes its VM, service account, project
IAM grants, configured notification gateways, per-agent storage access, model
access, and saved per-agent mappings.

Shared resources such as the VPC, subnet, NAT, budget, and storage buckets remain
while the fleet still uses them. `ghosty-agents clean-up --all-agents` removes
the fleet resources, while `--delete-project` is the explicit project-deletion
path. Storage-disable operations intentionally preserve user files.

## Repository layout

```text
.
├── pyproject.toml              # Python package metadata and CLI entry point
├── ghosty/                     # CLI and provisioning implementation
│   ├── cli.py                  # Typer command definitions
│   ├── interactive.py          # Interactive control room
│   ├── guided.py               # Guided agent creation workflow
│   ├── config.py               # Local configuration storage
│   ├── gcloud.py               # Isolated gcloud subprocess wrapper
│   ├── bootstrap.py            # Shared project and optional services
│   ├── agents.py               # Agent VM lifecycle
│   ├── doctor.py               # Readiness checks
│   └── models.py               # Configuration and inventory models
├── tests/                      # Unit tests with mocked gcloud calls
├── docs/                       # User, CLI, and infrastructure documentation
├── scripts/                    # Original Bash provisioning workflow
└── env.example.sh              # Example Bash-workflow configuration
```

The importable Python package is `ghosty`, while the installed executable is
`ghosty-agents`.

## Legacy Bash workflow

The Bash scripts provide the original single-agent, Mac/Linux deployment path.
They are useful for studying the individual provisioning steps, but the Python
CLI is the supported multi-agent experience. The Bash path does not configure
Cloud NAT.

```bash
cp env.example.sh env.sh
# Edit BILLING_ACCOUNT_ID, PROJECT_ID, and MY_ACCOUNT in env.sh.
source env.sh
./scripts/bootstrap/setup-config.sh
./scripts/bootstrap/00-create-project.sh
./scripts/bootstrap/01-link-billing.sh
./scripts/bootstrap/02-verify-billing.sh
./scripts/deploy/deploy-all.sh
./scripts/deploy/ssh-agent.sh
```

See the [billing guide](step-0-billing-and-project.md) and
[deployment runbook](runbook-deployment.md) for the individual steps.

## Contributor setup

Clone the repository and install it in an isolated development environment:

```bash
git clone https://github.com/castri1/ghosty-agents.git
cd ghosty-agents
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Run the test suite with:

```bash
pytest
```

Build the wheel and source archive with:

```bash
python -m pip install --upgrade build
python -m build
```

The GitHub release workflow validates that a pushed `vX.Y.Z` tag matches the
version in `pyproject.toml`, builds both distributions, and attaches them to a
GitHub Release. See the [distribution guide](github-distribution.md).

## Compatibility commands

Older technical command names such as `bootstrap`, `doctor`, `ssh`,
`google-chat`, `webhook`, `bucket`, `nat`, `up`, `list`, and `down` remain as
compatibility aliases. New automation should prefer the friendly command names
documented in the [CLI reference](cli.md).
