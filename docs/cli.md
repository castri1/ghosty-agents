# ghosty-agents CLI

A cross-platform (Windows/Linux/Mac) Python CLI that manages a fleet of private
agent machines. Run `ghosty-agents` to open the interactive control room, or use
the scriptable commands below. The importable package is `ghosty`.

## Install

Requires Python 3.11+ and the [`gcloud` CLI](https://cloud.google.com/sdk/docs/install)
(authenticated via `gcloud auth login`).

```bash
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
```

This installs the `ghosty-agents` command. (Tip: `alias ga=ghosty-agents`.)

## How it works

- **Config** is stored at the platform config dir (`typer.get_app_dir("ghosty-agents")`,
  e.g. `~/.config/ghosty-agents/config.toml` on Linux/Mac). It replaces the old
  `env.sh`. Override the location with `GHOSTY_CONFIG_DIR`.
- **Isolation**: all `gcloud` calls are pinned to a dedicated configuration
  (`--configuration ghosty-agents`) plus explicit `--account`/`--project`, so a
  concurrent `gcloud` session in another project is never disturbed.
- **Inventory source of truth is GCP**: agents are labelled
  `managed-by=ghosty-agents` + `ghosty-agent=<name>`, and `list` queries them live
  (no local state to drift).
- Each agent is one hardened VM (no external IP, Shielded VM, OS Login, dedicated
  least-privilege service account) reachable only through IAP.

## Commands

| Command | Description |
|---|---|
| `ghosty-agents` / `ghosty-agents console` | Open the interactive control room |
| `ghosty-agents init` | Friendly setup wizard: stores project, billing, account, and defaults. `--non-interactive` for scripted setup. |
| `ghosty-agents check` | Check local and cloud readiness (`--quick` skips GCP calls) |
| `ghosty-agents prepare` | One-time shared project preparation. Add `--with-nat` to enable shared internet. |
| `ghosty-agents internet status` / `enable` / `disable` | Manage shared internet for private agents |
| `ghosty-agents models status` / `enable` / `disable` | Let agents call Google AI models |
| `ghosty-agents chat status` / `add` / `remove` | Add or manage Google Chat for an agent |
| `ghosty-agents notifications status` / `add` / `sync` / `remove` | Add or manage external notifications for an agent |
| `ghosty-agents storage status` / `add` / `sync` / `disable` | Manage private per-agent storage, optional public folders, signed URL access, and Hermes env files |
| `ghosty-agents hermes install` / `configure` / `status` / `sync` | Install Hermes with the official installer, configure Google models, and check gateway state |
| `ghosty-agents create <name>` | Create an agent (`--startup-script`, `--with-hermes`, `-y`) |
| `ghosty-agents agents` | Inventory table |
| `ghosty-agents details <name>` | Details for one agent |
| `ghosty-agents connect <name>` | Connect to an agent (args after the name pass through, e.g. `connect worker-1 -- uptime`) |
| `ghosty-agents instruct <name> --service chat\|notifications\|storage\|models` | Send service setup instructions to Hermes over SSH/IAP |
| `ghosty-agents stop <name>` / `start <name>` | Stop/start to save cost |
| `ghosty-agents remove <name>` | Remove one agent, its cloud identity, and attached per-agent resources |
| `ghosty-agents clean-up` | Remove shared resources; `--all-agents`, `--delete-project` |
| `ghosty-agents settings show` / `set <key> <value>` | View/edit stored config |

Inside the interactive control room, select an agent and choose **View harness**
to open a local browser diagram of Connect, Chat, Notifications, Storage,
Models, and Internet for that agent. This view is read-only, binds only to
`127.0.0.1`, refreshes while it is open, and exits when you press `Ctrl+C` in
the CLI.

## Typical flow

```bash
ghosty-agents                      # open the control room
ghosty-agents init                 # enter project / billing / account
ghosty-agents check                # verify readiness
ghosty-agents prepare --with-nat   # one-time shared setup + outbound internet
ghosty-agents models enable        # optional: agents can call Google AI models
ghosty-agents storage add          # optional: private per-agent storage folders
ghosty-agents create worker-1 --with-hermes # spin up an agent + install Hermes
ghosty-agents hermes status worker-1 # check Hermes and gateway state
ghosty-agents chat add worker-1 --chat-project ghosty-agent-chat  # optional: Google Chat
ghosty-agents notifications add worker-1 --with-consumer # events reach Hermes
ghosty-agents instruct worker-1 --service notifications --name webhook
ghosty-agents create worker-2
ghosty-agents agents               # see the fleet
ghosty-agents connect worker-1     # connect to the agent
ghosty-agents stop worker-2        # pause to save cost
ghosty-agents remove worker-1      # remove one agent
ghosty-agents clean-up --all-agents  # remove everything (keeps project)
```

## Notes

- The first billable resource is the agent from `create` unless you opt into shared
  internet during `prepare --with-nat` or `internet enable`.
- A single project-level budget is created at `bootstrap`; per-agent cost is
  visible via label-filtered billing reports.
- Outbound internet: VMs have no external IP (Private Google Access reaches Google
  APIs). Use `ghosty-agents internet enable` if agents need general egress. The NAT
  is shared by private Ghosty VMs in the configured region/subnet and does not
  allow inbound public access.
- Models: `ghosty-agents models enable` enables the Agent Platform API
  (`aiplatform.googleapis.com`), grants existing Ghosty service accounts
  `roles/aiplatform.user`, and turns on the same grant for future agents.
- Storage: `ghosty-agents storage add` creates a private bucket
  (`<project-id>-ghosty-agent-storage` by default), enforces uniform bucket-level
  access and public access prevention, creates `agents/<agent>/` managed folders,
  grants each Ghosty agent service account `roles/storage.objectUser` only on
  its own folder, and writes `~/.config/hermes/storage.env` on running VMs.
  Add `--with-public` to create a separate public bucket with per-agent public
  managed folders, and `--with-signed-urls` to grant agents self-signing IAM for
  temporary links to private objects. Use `ghosty-agents storage sync [agent]`
  after starting or repairing a VM. `storage disable` removes IAM/env config but
  never deletes buckets, folders, or objects.
- Chat: Google Chat requires the Chat app and Pub/Sub topic to belong to
  the same GCP project. `ghosty-agents chat add <agent>
  --chat-project <project>` uses an existing Chat app project and saves that
  mapping for future `status`/`destroy` runs. If omitted, the CLI uses a saved
  mapping or derives `<main-project>-<agent>-chat` and creates the project unless
  `--no-create-project` is passed. Use `--chat-folder` and `--billing-account`
  to control project placement and billing for auto-created Chat projects.
  Resources are provisioned in the Chat project, while the VM key upload still
  targets the main Ghosty VM project. The gateway service account gets
  `roles/pubsub.subscriber` and `roles/pubsub.viewer` on the subscription, and
  the Google Chat publisher account gets `roles/pubsub.publisher` on the topic.
  You still configure the Google Chat API Console manually with the printed
  Cloud Pub/Sub topic.
- Hermes: `ghosty-agents hermes install <agent>` connects to the VM and uses the
  official Nous installer in a two-step form: download
  `https://hermes-agent.nousresearch.com/install.sh` to
  `/tmp/ghosty-hermes-install.sh`, then run it with
  `--skip-setup --non-interactive`. It then runs
  `"$HOME/.local/bin/hermes" gateway --accept-hooks install`. The default branch
  is `main`; use `--commit <sha>` only when you need a temporary reproducibility
  pin. `hermes configure` sets provider `vertex`, model
  `google/gemini-3.1-pro-preview`, project to the Ghosty project, and Vertex
  region `global`, relying on the VM service account instead of API keys.
  `create <agent> --with-hermes` performs install + configure after VM creation.
- Notifications: `ghosty-agents notifications add <agent>` starts a guided setup for a
  public Cloud Run receiver, Pub/Sub topic/subscription, receiver publisher IAM,
  agent subscriber/viewer IAM, and `~/.config/hermes/webhooks/<name>.env` on the
  VM. External systems send the shared secret in `X-Ghosty-Webhook-Secret`.
  The CLI also grants `roles/run.builder` to the project build service account
  so `gcloud run deploy --source` can package the bundled receiver. Scripted
  runs can still pass `--name`, `--secret`, `--generate-secret`, and `--yes`.
  Add `--with-consumer` during `notifications add` or `notifications sync` to
  install a managed user service on the VM. That consumer pulls Pub/Sub events
  with the VM service account, saves each event under
  `~/.config/hermes/inbox/events/<name>/`, acknowledges after the durable write,
  then invokes Hermes with a bounded one-shot prompt. Cloud NAT is not required,
  and no inbound VM access is opened.
- Agent briefings: add `--brief-agent` to supported setup/sync commands, or run
  `ghosty-agents instruct <agent> --service chat|notifications|storage|models`
  later. Ghosty writes a generated prompt to `~/.config/hermes/inbox` on the VM
  and runs `agent_instruction_command` over SSH/IAP with a timeout. The default is
  `"$HOME/.local/bin/hermes" -z "$(cat "$GHOSTY_PROMPT_FILE")"`; override it with
  `ghosty-agents settings set agent_instruction_command '<command>'`. The default
  timeout is 600 seconds; override it with
  `ghosty-agents settings set agent_instruction_timeout_seconds 900`.
- Agent application/runtime: use `create --with-hermes` or `hermes sync <agent>`
  for Hermes. For other runtime setup, pass `--startup-script <path>` to
  `create`, or provision after first connect.
- Agent removal: `ghosty-agents remove <agent>` deletes the VM, its service
  account, project IAM grants, configured notification gateways, per-agent
  storage IAM/managed folders, model access, and saved per-agent config
  mappings. Shared fleet resources such as NAT, VPC/subnet, and shared buckets
  remain while other agents still use them; use `ghosty-agents clean-up
  --all-agents` when intentionally removing the whole fleet.
- Compatibility: older technical commands like `bootstrap`, `doctor`, `ssh`,
  `google-chat`, `webhook`, `bucket`, `nat`, `up`, `list`, and `down` still work,
  but the friendly names above are the primary UX.
