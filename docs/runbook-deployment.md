# Hardened Deployment Runbook (section 2 onward)

This is the phase after Step 0 (billing + project bootstrap). It provisions a
single, hardened agent VM reachable **only** through Identity-Aware Proxy (IAP),
plus a budget alert so cost can't run away unnoticed.

> Prerequisites (from Step 0):
> - `env.sh` filled in and `source`d
> - `./scripts/bootstrap/setup-config.sh` run (isolated gcloud config exists)
> - project exists and billing is linked (`./scripts/bootstrap/02-verify-billing.sh` green)

## Security model

```
   you (gcloud, authenticated)
        │  roles/iap.tunnelResourceAccessor + roles/compute.osLogin
        ▼
   Identity-Aware Proxy  ──tunnel──►  VM:22
        ▲                              • no external IP (--no-address)
        │ firewall: allow tcp:22 ONLY  • Shielded VM (secure boot + vTPM)
        │ from 35.235.240.0/20         • OS Login (IAM SSH keys)
   everything else: deny-all ingress   • dedicated least-privilege SA
```

No public IP + IAP-only firewall means the box is never exposed to the open
internet. OS Login means SSH access is governed by IAM (revoke by removing a
role), not by static keys.

## Steps

Each script is **idempotent** and routes through the isolated gcloud config
(`gc()` wrapper in `lib.sh`), so it's safe alongside your other gcloud session.

| # | Script | What it creates | Cost |
|---|--------|-----------------|------|
| 10 | `10-enable-apis.sh` | Enables compute/iap/oslogin/iam/logging/monitoring/billing APIs | free |
| 11 | `11-service-account.sh` | Dedicated VM service account (logWriter + metricWriter only) | free |
| 12 | `12-network.sh` | Custom VPC + one subnet (Private Google Access on) | free |
| 13 | `13-firewall-iap.sh` | `allow-ssh-from-iap` (35.235.240.0/20) + `deny-all-ingress` | free |
| 14 | `14-iam-iap.sh` | Grants **your** account IAP tunnel + OS Login + actAs SA | free |
| 15 | `15-create-vm.sh` | The hardened VM (no IP, Shielded, OS Login) | **billable** |
| 16 | `16-budget-alert.sh` | Budget on the dedicated billing account (50/90/100%) | free |

### Run it

One step at a time (recommended the first time):

```bash
source env.sh
./scripts/deploy/10-enable-apis.sh
./scripts/deploy/11-service-account.sh
./scripts/deploy/12-network.sh
./scripts/deploy/13-firewall-iap.sh
./scripts/deploy/14-iam-iap.sh
./scripts/deploy/15-create-vm.sh        # prompts before creating the billable VM
./scripts/deploy/16-budget-alert.sh
```

Or all at once (single confirmation, then runs 10→16):

```bash
source env.sh
./scripts/deploy/deploy-all.sh
```

### Connect to the VM

```bash
./scripts/deploy/ssh-agent.sh           # interactive shell via IAP
./scripts/deploy/ssh-agent.sh -- uptime # run a one-off remote command
```

### Tear down (stop cost)

```bash
./scripts/deploy/99-teardown.sh  # deletes VM + network + SA (keeps project/budget)
# or, to remove everything:
gcloud projects delete ghosty-agents
```

## Notes & knobs

- **Outbound internet:** the VM has no external IP. It can reach Google APIs via
  Private Google Access, but for general outbound (e.g. `apt` from public repos)
  you'd add **Cloud NAT**. The Python CLI can manage this with
  `ghosty-agents bootstrap --with-nat` or `ghosty-agents nat enable`. NAT is a
  shared regional outbound-only component for private VMs in the configured
  subnet; it is billable and does not expose inbound SSH or public VM IPs.
- **Google AI models:** if agents should call Gemini Enterprise Agent Platform
  / Vertex AI models using their VM service accounts, run
  `ghosty-agents google-ai enable`. This enables `aiplatform.googleapis.com`,
  grants `roles/aiplatform.user` to existing Ghosty agent service accounts, and
  applies the same grant to future agents.
- **Agent storage:** if agents need durable object storage, run
  `ghosty-agents bucket setup`. The CLI creates a regional private Cloud Storage
  bucket with public access prevention, creates one managed folder per agent,
  grants `roles/storage.objectUser` only on that agent's folder, and writes
  `~/.config/hermes/storage.env` on running VMs. Add `--with-public` for a
  separate public bucket with per-agent public folders, and `--with-signed-urls`
  for temporary signed links to private objects. `bucket disable` removes
  access/config but keeps buckets and data.
- **Hermes runtime:** to install Hermes on a VM, run
  `ghosty-agents hermes install <agent>` or create the VM with
  `ghosty-agents create <agent> --with-hermes`. The CLI uses the official Nous
  installer by downloading `https://hermes-agent.nousresearch.com/install.sh`
  on the VM and running it with `--skip-setup --non-interactive`, then starts
  the gateway with `"$HOME/.local/bin/hermes" gateway --accept-hooks install`.
  `ghosty-agents hermes configure <agent>` sets Vertex defaults for Google
  models using the VM service account, so no personal API key is required.
- **Google Chat gateway:** if Hermes should receive Google Chat events through
  Pub/Sub, run `ghosty-agents google-chat setup <agent> --chat-project
  <chat-project>` after the VM exists. Google Chat requires the Chat app and
  Pub/Sub topic to be in the same project, so the CLI provisions the
  topic/subscription, gateway service account, JSON key, and Pub/Sub IAM in the
  Chat project. It still uploads the key to the VM in the main Ghosty project.
  If `--chat-project` is omitted, the CLI uses a saved per-agent mapping or
  derives and creates a per-agent Chat project. Visibility, 1:1/group toggles,
  and installing the app in a space remain manual Google Workspace steps.
- **Webhook gateway:** if external systems need to notify a private agent, run
  `ghosty-agents webhook setup <agent>` and answer the name/secret prompts. The
  CLI deploys a public Cloud Run receiver that validates the
  `X-Ghosty-Webhook-Secret` header and publishes events to Pub/Sub. The VM keeps
  no external IP and consumes from the pull subscription using outbound Google
  API access. Hermes or another worker reads the VM env file at
  `~/.config/hermes/webhooks/<name>.env`. During setup, the CLI grants
  `roles/run.builder` to the project build service account used by Cloud Run
  source deployments. Add `--with-consumer` to `webhook setup` or
  `webhook sync` when you want Ghosty to install the managed VM-side consumer
  that writes events to `~/.config/hermes/inbox/events/<name>/` and invokes
  Hermes after each durable write.
- **Brief Hermes after setup:** add `--brief-agent` to supported setup/sync
  commands, or run `ghosty-agents instruct <agent> --service <service>`. The CLI
  uploads a generated prompt to `~/.config/hermes/inbox` and runs the configured
  `agent_instruction_command` over SSH/IAP with a timeout. By default this is
  `"$HOME/.local/bin/hermes" -z "$(cat "$GHOSTY_PROMPT_FILE")"`. The default
  timeout is 600 seconds; set `agent_instruction_timeout_seconds` if a provider
  needs longer.
- **Need sudo over SSH?** In `14-iam-iap.sh`, swap `roles/compute.osLogin` for
  `roles/compute.osAdminLogin`.
- **Sizing:** change `MACHINE_TYPE`, `BOOT_DISK_SIZE`, etc. in `env.sh`.
- **Budgets notify, they don't cap.** A budget alert emails you; it does not stop
  spend. For a hard stop you'd wire the budget's Pub/Sub topic to an automation
  that disables billing — out of scope here.
- **Other agent software** can still be installed with a startup script. Add a
  `--metadata-from-file startup-script=...` to `15-create-vm.sh`, or provision
  after first SSH.
