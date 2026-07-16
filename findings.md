# Ghosty Agent Provisioning Findings

Date: 2026-07-04

## Summary

Created a new Ghosty agent named `mira-sol` with the project CLI, attached the non-human-intervention primitives, installed Hermes, configured it for Google Gemini 3.1 Pro through Vertex AI, and verified the important paths from the VM side.

Google Chat was intentionally skipped because it still needs Google Console / Workspace human setup.

## Agent

- Agent: `mira-sol`
- Instance: `ghosty-mira-sol`
- Zone: `us-east1-b`
- Machine: `e2-medium`
- Internal IP: `10.10.0.3`
- Service account: `ghosty-mira-sol-sa@ghosty-agents.iam.gserviceaccount.com`
- Status: `RUNNING`
- External VM IP: none. GCP instance description shows only the private NIC on `ghosty-vpc/ghosty-subnet`.

Commands used:

```bash
.venv/bin/ghosty-agents create mira-sol --yes
.venv/bin/ghosty-agents details mira-sol
```

## Primitives

### Internet

- Shared internet is enabled through Cloud NAT.
- Router: `ghosty-router`
- NAT: `ghosty-nat`
- Region: `us-east1`
- Verified from `mira-sol` with `curl -I https://github.com`, which returned HTTP 200.
- Inbound access remains closed; VM still has no external IP.

### Models

- Project API: `aiplatform.googleapis.com` enabled.
- Future-agent auto-grant: enabled.
- `mira-sol` service account has `roles/aiplatform.user`.
- Hermes model config:
  - Provider: `vertex`
  - Model: `google/gemini-3.1-pro-preview`
  - Vertex project: `ghosty-agents`
  - Vertex region: `global`
- Verified from inside the VM:

```bash
hermes --provider vertex -m google/gemini-3.1-pro-preview -z "Reply with exactly: GHOSTY_MODEL_OK"
```

Result: `GHOSTY_MODEL_OK`

No user API key was needed for this path because Hermes can use the VM service account through Vertex AI. If you prefer Google AI Studio instead, place a `GOOGLE_API_KEY` or `GEMINI_API_KEY` in `~/.hermes/.env` and switch Hermes back to provider `gemini`.

### Storage

- Private bucket: `gs://ghosty-agents-ghosty-agent-storage/agents/mira-sol/`
- Public bucket: `gs://ghosty-agents-ghosty-agent-public/agents/mira-sol/`
- VM env file: `~/.config/hermes/storage.env`
- Signed URL signing account: `ghosty-mira-sol-sa@ghosty-agents.iam.gserviceaccount.com`
- Verified from inside the VM:
  - private object upload/read/delete succeeded.
  - public object upload/anonymous read/delete succeeded.
  - IAM Credentials `signBlob` succeeded for signed-link support.

### Notifications

- Notification name: `intake`
- Cloud Run receiver: `ghosty-mira-sol-webhook-intake`
- Public URL: `https://ghosty-mira-sol-webhook-intake-3oahiorpea-ue.a.run.app`
- Secret header: `X-Ghosty-Webhook-Secret`
- Secret value: redacted.
- Pub/Sub subscription: `projects/ghosty-agents/subscriptions/mira-sol-webhook-intake-events-sub`
- VM env file: `~/.config/hermes/webhooks/intake.env`

Verified in three layers:

1. External HTTPS POST returned `{"ok": true, "message_id": ...}`.
2. Pulling from the VM with the VM service account returned the expected event envelope.
3. Installed a VM-side consumer service so Hermes receives notifications automatically.

Consumer service:

- Script: `~/.local/bin/ghosty-intake-consumer`
- Service: `ghosty-intake-consumer.service`
- Event inbox: `~/.config/hermes/inbox/events/intake/`
- Behavior: pulls Pub/Sub, writes each event JSON into the Hermes inbox, acknowledges after the event is durable, then invokes Hermes with a bounded one-shot prompt.

Final verification event:

- Message id: `20545164509871464`
- Saved event: `~/.config/hermes/inbox/events/intake/20545164509871464.json`
- Hermes result log: `~/.config/hermes/inbox/events/intake/20545164509871464.log`
- Result: Hermes completed with return code `0`.
- Duplicate check: the final message id appeared once in the consumer journal.

## Hermes Install

- Install path: `~/.hermes/hermes-agent`
- Source repo: `https://github.com/NousResearch/hermes-agent.git`
- Checked-out commit: `eae3700b1`
- Hermes version reported: `Hermes Agent v0.17.0`
- CLI path: `~/.local/bin/hermes`
- Env path: `~/.hermes/.env`
- Config path: `~/.hermes/config.yaml`
- Gateway service: `hermes-gateway.service`
- Gateway status: active/running.
- Linger: enabled, so the user service survives SSH logout.

## Fixes Applied

### Storage Sync Timing

The initial `create mira-sol` run created storage folders and IAM but could not write `storage.env` while the VM was still fresh. Running:

```bash
.venv/bin/ghosty-agents storage sync mira-sol
```

fixed it. Later `storage status` showed `VM ENV = YES`.

### Hermes Instruction Command

The previous Ghosty default used:

```bash
hermes run "$(cat "$GHOSTY_PROMPT_FILE")"
```

That does not work with this Hermes build because there is no `run` subcommand.

The first correction used:

```bash
hermes -z "$(cat "$GHOSTY_PROMPT_FILE")"
```

That worked interactively, but failed over non-interactive SSH because `~/.local/bin` was not on PATH.

Final corrected command:

```bash
"$HOME/.local/bin/hermes" -z "$(cat "$GHOSTY_PROMPT_FILE")"
```

Applied in:

- `ghosty/models.py`
- `README.md`
- `docs/cli.md`
- `docs/runbook-deployment.md`
- `tests/test_instructions.py`
- live Ghosty config at `/Users/daniel/Library/Application Support/ghosty-agents/config.toml`

Storage briefing then succeeded:

```bash
.venv/bin/ghosty-agents instruct mira-sol --service storage
```

### Notification Consumer Ack Timing

The first version of the VM notification consumer acknowledged Pub/Sub only after Hermes finished. Hermes took longer than Pub/Sub's ack deadline, so the same message was redelivered and processed multiple times.

Fix: save the event to Hermes inbox, acknowledge immediately after the event is durable, then invoke Hermes. A failed Hermes run leaves the saved event and log for retry/inspection without causing Pub/Sub redelivery loops.

## Verification Commands

```bash
.venv/bin/ghosty-agents agents
.venv/bin/ghosty-agents details mira-sol
.venv/bin/ghosty-agents internet status
.venv/bin/ghosty-agents models status
.venv/bin/ghosty-agents storage status
.venv/bin/ghosty-agents notifications status mira-sol --name intake
.venv/bin/python -m pytest -q
```

Final test result:

```text
108 passed in 1.13s
```

## Remaining Notes

- Google Chat was not configured because it needs manual Google Console / Workspace steps.
- The notification URL and secret are stored in Ghosty's local config. Do not paste the secret into docs or chat.
- The generic notification consumer is installed directly on `mira-sol`; if this pattern should become standard, it should be promoted into a first-class Ghosty CLI feature instead of remaining VM-local setup.

---

# Guided Setup End-to-End Run - 2026-07-06

## Objective

Deploy one new throwaway Ghosty agent through the guided CLI setup, attach every primitive that does not require human intervention, verify every service connection green with the CLI and gcloud, and fix the CLI wherever it breaks.

## Safety Scope

- Target project: `ghosty-agents`.
- Explicit gcloud proof commands will use `--configuration ghosty-agents --project ghosty-agents --account daniel@melonn.com` because the ambient gcloud default project is not `ghosty-agents`.
- Existing agent `alba-nury` must not be changed.
- New throwaway agent selected for this run: `e2e-luma`.

## Initial State

Command:

```bash
.venv/bin/ghosty-agents settings show
```

Important output:

```text
project_id = ghosty-agents
account = daniel@melonn.com
region = us-east1
zone = us-east1-b
google_ai_enabled = True
storage_enabled = True
storage_public_enabled = True
storage_signed_urls_enabled = True
machine_type = e2-medium
```

Command:

```bash
.venv/bin/ghosty-agents agents
```

Output:

```text
Ghost fleet - ghosty-agents (1/1 awake)
alba-nury RUNNING us-east1-b e2-medium 10.10.0.2
```

Command:

```bash
.venv/bin/ghosty-agents internet status
.venv/bin/ghosty-agents models status
.venv/bin/ghosty-agents storage status
```

Important output:

```text
Shared internet: ENABLED
Google AI API enabled: YES
Future-agent auto-grant: YES
Shared storage: private bucket YES, public bucket YES, signed URLs YES
```

## Guided Setup Attempt 1

Command:

```bash
printf '1\nY\nY\nY\nY\nY\nN\nY\ne2e-intake\nY\nY\nY\nY\n' | .venv/bin/ghosty-agents create e2e-luma --guided
```

Result:

- The guided setup created `e2e-luma`, installed/configured Hermes, created the notification receiver, installed the notification consumer, and briefed Hermes about models/storage/notifications.
- Google Chat was skipped because it requires manual Google Console / Workspace work.
- The run exposed a CLI bug: storage env upload was attempted too early on the fresh VM and printed a warning before later setup succeeded.

Fix recorded in `findings_2.md`:

- Removed duplicate low-level storage sync from `agents.create_agent`.
- Added retry behavior to `bootstrap.upload_storage_env_to_agent`.
- Updated retry guidance to use the friendly `storage sync` command.

Test validation after fix:

```text
134 passed in 1.33s
```

This first attempt is not the final clean proof. I will remove `e2e-luma` and rerun guided setup after the fix.

## Cleanup Before Clean Run

Command:

```bash
.venv/bin/ghosty-agents remove e2e-luma --yes
```

Important output:

```text
Removing notifications 'e2e-intake' for 'e2e-luma'
Deleting Cloud Run service 'ghosty-e2e-luma-webhook-e2e-intake'
Deleting Pub/Sub subscription 'e2e-luma-webhook-e2e-intake-events-sub'
Deleting Pub/Sub topic 'e2e-luma-webhook-e2e-intake-events'
Removing storage access for 'e2e-luma'
Removing model access for 'e2e-luma'
Deleting VM 'ghosty-e2e-luma'
service account deleted
agent 'e2e-luma' removed
```

Verification:

```text
.venv/bin/ghosty-agents agents -> only alba-nury remains.
gcloud compute instances describe ghosty-e2e-luma -> not found.
gcloud pubsub topics describe e2e-luma-webhook-e2e-intake-events -> not found.
```

## Guided Setup Attempt 2

Command:

```bash
set -o pipefail
printf '1\nY\nY\nY\nY\nY\nN\nY\ne2e-intake\nY\nY\nY\nY\n' | .venv/bin/ghosty-agents create e2e-luma --guided 2>&1 | tee /tmp/ghosty-e2e-clean.log
```

Result:

- The storage timing fix worked: the CLI printed a retry notice instead of a false failure.
- Hermes, models, storage, notification receiver, notification consumer, model briefing, and storage briefing completed.
- Notification briefing exceeded the previous 300 second timeout and failed as an optional step.

Follow-up fix recorded in `findings_2.md`:

- Added configurable `agent_instruction_timeout_seconds` with default `600`.
- Improved instruction failure messages so timeouts are explicit.

Validation:

```bash
.venv/bin/ghosty-agents instruct e2e-luma --service notifications --name e2e-intake
```

Output:

```text
agent 'e2e-luma' was briefed about notifications
Prompt path on agent: ~/.config/hermes/inbox/notifications-setup.md
```

This second attempt is not the final clean proof because the guided setup itself needed a retry. I will remove `e2e-luma` and rerun guided setup after the timeout fix.

## Guided Setup Attempt 3

Command:

```bash
set -o pipefail
printf '1\nY\nY\nY\nY\nY\nN\nY\ne2e-intake\nY\nY\nY\nY\n' | .venv/bin/ghosty-agents create e2e-luma --guided 2>&1 | tee /tmp/ghosty-e2e-final.log
```

Result:

- Storage retry behavior worked.
- Hermes install hit a fresh-VM SSH readiness race and failed before the installer actually ran.
- Notification infrastructure was created, but webhook env upload also reported a fresh-VM write failure.
- The guided summary then crashed on a Rich `MarkupError` because gcloud output contained `[/usr/bin/ssh]`.

Fix recorded in `findings_2.md`:

- Escaped untrusted console output before Rich renders it.
- Added transient SSH retry behavior to Hermes install.
- Added transient SSH retry behavior to notification env upload.

Validation:

```text
140 passed in 1.35s
```

This third attempt is not the final clean proof. I will remove `e2e-luma` and rerun guided setup after the console escaping and Hermes install retry fixes.

## Final Clean Guided Setup Run

Command:

```bash
set -o pipefail
printf '1\nY\nY\nY\nY\nY\nN\nY\ne2e-intake\nY\nY\nY\nY\n' | .venv/bin/ghosty-agents create e2e-luma --guided 2>&1 | tee /tmp/ghosty-e2e-final2.log
```

Result:

- Guided setup completed successfully for a fresh throwaway agent, `e2e-luma`.
- `alba-nury` was not modified and remained running.
- Shared internet was reused.
- Models were already enabled and the new VM service account received model access.
- Private storage, public sharing, and signed links were configured for the agent-scoped folder.
- Hermes installed through the vendor installer, was configured for Vertex, and the gateway was running.
- A public HTTPS notification address was created through Cloud Run.
- The VM-side notification consumer was installed and running.
- The agent was briefed about models, storage, and notifications.
- Google Chat was intentionally skipped in this guided run because it requires manual Google Console / Workspace configuration.
- A log scan found no `Needs retry`, `failed`, `Traceback`, `Exception`, `Could not`, `ERROR`, or Rich failure markers in `/tmp/ghosty-e2e-final2.log`.

CLI verification:

```text
.venv/bin/ghosty-agents agents
-> alba-nury RUNNING, e2e-luma RUNNING

.venv/bin/ghosty-agents hermes status e2e-luma
-> installed YES, command YES, environment YES, config YES, gateway RUNNING
-> provider vertex, model google/gemini-3.1-pro-preview

.venv/bin/ghosty-agents storage status
-> e2e-luma private YES, private IAM YES, public YES, public IAM YES,
   public read YES, signed URL YES, legacy broad NO, VM env YES

.venv/bin/ghosty-agents models status
-> API enabled YES, future-agent auto-grant YES, e2e-luma HAS ROLE YES

.venv/bin/ghosty-agents notifications status e2e-luma --name e2e-intake
-> URL present, TOPIC YES, SUB YES, RUN YES, PUB/SUB YES, IAM YES,
   VM ENV YES, CONSUMER RUNNING
```

GCP verification:

```text
gcloud compute instances describe ghosty-e2e-luma
-> RUNNING, internal IP 10.10.0.8, NAT_IP blank, service account
   ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com

gcloud run services describe ghosty-e2e-luma-webhook-e2e-intake
-> URL https://ghosty-e2e-luma-webhook-e2e-intake-3oahiorpea-ue.a.run.app
-> Ready=True, ConfigurationsReady=True, RoutesReady=True

gcloud pubsub subscriptions describe e2e-luma-webhook-e2e-intake-events-sub
-> ACTIVE pull subscription on topic
   projects/ghosty-agents/topics/e2e-luma-webhook-e2e-intake-events

gcloud compute routers nats describe ghosty-nat
-> PUBLIC, AUTO_ONLY, LIST_OF_SUBNETWORKS, ALL_IP_RANGES

gcloud projects get-iam-policy ghosty-agents
-> roles/aiplatform.user for
   serviceAccount:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com

gcloud pubsub topics get-iam-policy e2e-luma-webhook-e2e-intake-events
-> roles/pubsub.publisher for
   serviceAccount:e2e-luma-e2e-intake-wh-run-sa@ghosty-agents.iam.gserviceaccount.com

gcloud pubsub subscriptions get-iam-policy e2e-luma-webhook-e2e-intake-events-sub
-> roles/pubsub.subscriber and roles/pubsub.viewer for
   serviceAccount:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com

gcloud storage managed-folders get-iam-policy gs://ghosty-agents-ghosty-agent-storage/agents/e2e-luma/
-> roles/storage.objectUser for
   serviceAccount:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com

gcloud storage managed-folders get-iam-policy gs://ghosty-agents-ghosty-agent-public/agents/e2e-luma/
-> roles/storage.objectUser for the agent service account
-> roles/storage.objectViewer for allUsers

gcloud iam service-accounts get-iam-policy ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com
-> roles/iam.serviceAccountTokenCreator self-grant for signed URLs
```

VM verification:

```text
~/.config/hermes/storage.env contains:
GHOSTY_BUCKET=ghosty-agents-ghosty-agent-storage
GHOSTY_BUCKET_URI=gs://ghosty-agents-ghosty-agent-storage/agents/e2e-luma/
GHOSTY_PUBLIC_BUCKET=ghosty-agents-ghosty-agent-public
GHOSTY_PUBLIC_BUCKET_URI=gs://ghosty-agents-ghosty-agent-public/agents/e2e-luma/
GHOSTY_PUBLIC_BASE_URL=https://storage.googleapis.com/ghosty-agents-ghosty-agent-public/agents/e2e-luma/
GHOSTY_SIGNING_SERVICE_ACCOUNT=ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com
GOOGLE_CLOUD_PROJECT=ghosty-agents
SIGNED_URLS=enabled

~/.config/hermes/webhooks/e2e-intake.env contains:
GHOSTY_WEBHOOK_NAME=e2e-intake
GHOSTY_WEBHOOK_PROVIDER=generic
GHOSTY_WEBHOOK_SUBSCRIPTION=projects/ghosty-agents/subscriptions/e2e-luma-webhook-e2e-intake-events-sub
GHOSTY_WEBHOOK_TOPIC=projects/ghosty-agents/topics/e2e-luma-webhook-e2e-intake-events
GHOSTY_WEBHOOK_EVENT_FORMAT=ghosty.webhook.v1
GOOGLE_CLOUD_PROJECT=ghosty-agents
GHOSTY_WEBHOOK_URL=https://ghosty-e2e-luma-webhook-e2e-intake-3oahiorpea-ue.a.run.app

systemctl --user is-active hermes-gateway.service -> active
systemctl --user is-active ghosty-e2e-intake-consumer.service -> active
```

Webhook delivery proof:

```text
curl -H X-Ghosty-Webhook-Secret:<redacted> ... https://ghosty-e2e-luma-webhook-e2e-intake-3oahiorpea-ue.a.run.app
-> {"message_id":"20548701938974919","ok":true}
-> HTTP:200

VM event file:
~/.config/hermes/inbox/events/e2e-intake/20548701938974919.json
-> contains "check": "ghosty-e2e-1783364454"

VM Hermes event log:
~/.config/hermes/inbox/events/e2e-intake/20548701938974919.log
-> {"returncode": 0, "status": "completed", ...}
```

Final test suite:

```text
.venv/bin/python -m pytest -q
-> 140 passed in 1.41s
```
