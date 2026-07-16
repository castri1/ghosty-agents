# Guided Setup Bug/Fix Log - 2026-07-06

This file records every checkpoint tested in the current guided end-to-end run, the exact Ghosty CLI commands and important output, every CLI bug found and fixed, and any primitive skipped with the reason.

## Run Scope

- Project: `ghosty-agents`.
- Existing protected agent: `alba-nury`.
- Throwaway test agent: `e2e-luma`.
- Agent model target: Gemini 3.1 Pro through Hermes/Vertex configuration.

## Checkpoints

## Checkpoint 1 - Guided Create Attempt Exposed Fresh-VM Storage Timing Bug

Command:

```bash
printf '1\nY\nY\nY\nY\nY\nN\nY\ne2e-intake\nY\nY\nY\nY\n' | .venv/bin/ghosty-agents create e2e-luma --guided
```

Important output:

```text
How much should Ghosty set up?
1) Recommended setup
2) Create only
...
Agent              e2e-luma
Hermes             install and configure
Models             enable Google model access
Files              private file space + public sharing + temporary links
Chat               skip
Notifications      add notification address 'e2e-intake' + event listener
Shared internet    already available
...
agent 'e2e-luma' created
Creating managed folder 'gs://ghosty-agents-ghosty-agent-storage/agents/e2e-luma/'
Granting roles/storage.objectUser ...
Could not write storage env on 'e2e-luma'. Rerun `ghosty-agents bucket sync e2e-luma`.
...
Hermes installed on 'e2e-luma'
Hermes model configured on 'e2e-luma'
Deploying Cloud Run receiver 'ghosty-e2e-luma-webhook-e2e-intake'
Installing notification consumer 'ghosty-e2e-intake-consumer.service' on 'e2e-luma'
agent 'e2e-luma' was briefed about models
agent 'e2e-luma' was briefed about storage
agent 'e2e-luma' was briefed about notifications
Agent 'e2e-luma' is ready.
```

Secret output was intentionally redacted from this file.

Bug:

- A fresh VM can report `RUNNING` before OS Login/IAP SSH is fully ready. The low-level `agents.create_agent` helper immediately tried to sync storage and write `~/.config/hermes/storage.env`, causing a false warning during guided setup.
- The warning also used the old hidden wording `bucket sync` instead of the friendly `storage sync`.
- The storage setup later succeeded, so this was a timing/UX bug, not an IAM or bucket failure.

Fix:

- Removed the hidden duplicate storage sync from `agents.create_agent`; user-facing create/guided flows own post-create service attachment.
- Added retry behavior to `bootstrap.upload_storage_env_to_agent` so fresh-VM storage env writes wait for SSH readiness before reporting failure.
- Changed retry guidance from `ghosty-agents bucket sync` to `ghosty-agents storage sync`.
- Added unit coverage:
  - `test_create_agent_leaves_storage_sync_to_callers`
  - `test_upload_storage_env_retries_until_fresh_vm_accepts_ssh`

Validation:

```bash
.venv/bin/python -m pytest -q
```

Output:

```text
134 passed in 1.33s
```

Status:

- This first guided run is not accepted as the final proof because it emitted the storage warning before the fix.
- Next step: remove `e2e-luma`, recreate it through guided setup, and use that clean run as the final checkpoint proof.

## Checkpoint 2 - Guided Create Attempt Exposed Hermes Briefing Timeout/Message Bug

Command:

```bash
set -o pipefail
printf '1\nY\nY\nY\nY\nY\nN\nY\ne2e-intake\nY\nY\nY\nY\n' | .venv/bin/ghosty-agents create e2e-luma --guided 2>&1 | tee /tmp/ghosty-e2e-clean.log
```

Important output:

```text
Agent              e2e-luma
Hermes             install and configure
Models             enable Google model access
Files              private file space + public sharing + temporary links
Chat               skip
Notifications      add notification address 'e2e-intake' + event listener
Shared internet    already available
...
agent 'e2e-luma' created
agent 'e2e-luma' is not ready for storage config yet; retrying...
Hermes installed on 'e2e-luma'
Hermes model configured on 'e2e-luma'
Deploying Cloud Run receiver 'ghosty-e2e-luma-webhook-e2e-intake'
Installing notification consumer 'ghosty-e2e-intake-consumer.service' on 'e2e-luma'
agent 'e2e-luma' was briefed about models
agent 'e2e-luma' was briefed about storage
Hermes briefing failed on 'e2e-luma'. Prompt is on the VM at ~/.config/hermes/inbox/notifications-setup.md.
Brief agent about notifications did not finish. You can retry later.
```

Secret output was intentionally redacted from this file.

Bug:

- The fresh-VM storage fix worked: the CLI retried instead of emitting the previous false storage failure.
- The notification briefing exceeded the hard-coded 300 second instruction timeout.
- The CLI failure message was poor because `gcloud compute ssh` wrote the IAP NumPy performance warning to stderr, so Ghosty surfaced that warning instead of the timeout/remote exit code.

Fix:

- Added `agent_instruction_timeout_seconds = 600` to config defaults.
- Changed instruction delivery to use the config timeout when building the remote `timeout ... bash -lc ...` command.
- Improved instruction delivery failure messages so timeout exits explicitly say `Hermes command timed out after <N> seconds` and include the SSH return code/stdout/stderr context.
- Updated README, CLI docs, and deployment runbook with the timeout setting.
- Added unit coverage:
  - `test_deliver_instruction_uses_configured_timeout`
  - `test_deliver_instruction_reports_timeout_clearly`

Validation:

```bash
.venv/bin/python -m pytest -q
```

Output:

```text
136 passed in 1.24s
```

## Checkpoint 3 - Guided Summary Crashed On Unescaped SSH Error; Hermes Install Needed Fresh-VM Retry

Command:

```bash
set -o pipefail
printf '1\nY\nY\nY\nY\nY\nN\nY\ne2e-intake\nY\nY\nY\nY\n' | .venv/bin/ghosty-agents create e2e-luma --guided 2>&1 | tee /tmp/ghosty-e2e-final.log
```

Important output:

```text
agent 'e2e-luma' created
agent 'e2e-luma' is not ready for storage config yet; retrying...
agent 'e2e-luma' is not ready for storage config yet; retrying...
Installing Hermes on 'e2e-luma'
Hermes install failed on 'e2e-luma'
Hermes did not finish. You can retry later.
...
Could not write webhook env on 'e2e-luma'. Rerun `ghosty-agents webhook sync e2e-luma --name e2e-intake`.
...
MarkupError: closing tag '[/usr/bin/ssh]' at position 1996 doesn't match any open tag
```

Bug:

- The guided setup summary crashed because `ui.warn()` rendered untrusted gcloud output as Rich markup. The string `[/usr/bin/ssh]` was interpreted as a closing Rich tag.
- Hermes install failed quickly in the guided flow, but a standalone retry on the same VM succeeded:

```bash
.venv/bin/ghosty-agents hermes install e2e-luma --yes
```

Output:

```text
Installing Hermes on 'e2e-luma'
Hermes installed on 'e2e-luma'
```

- Root cause: another fresh-VM SSH readiness race. The guided flow reached the vendor installer before `gcloud compute ssh` was reliable and treated SSH return code 255 as final.

Fix:

- Escaped untrusted text in `ui.step`, `ui.success`, `ui.skip`, `ui.warn`, `ui.error`, and `ui.info` before passing it to Rich.
- Added Hermes install retry behavior for transient SSH failures (`returncode=255` or matching SSH/IAP failure hints).
- Added notification env upload retry behavior for the same fresh-VM SSH readiness race, and changed its retry hint to the friendly `notifications sync` command.
- Added unit coverage:
  - `test_warning_escapes_untrusted_rich_markup`
  - `test_error_escapes_untrusted_rich_markup`
  - `test_install_hermes_retries_fresh_vm_ssh_race`
  - `test_upload_webhook_env_retries_until_fresh_vm_accepts_ssh`

Validation:

```bash
.venv/bin/python -m pytest -q
```

Output:

```text
140 passed in 1.35s
```

## Checkpoint 4 - Final Clean Guided Setup Passed End To End

Command:

```bash
set -o pipefail
printf '1\nY\nY\nY\nY\nY\nN\nY\ne2e-intake\nY\nY\nY\nY\n' | .venv/bin/ghosty-agents create e2e-luma --guided 2>&1 | tee /tmp/ghosty-e2e-final2.log
```

Input path:

- Recommended setup.
- Hermes: yes.
- Models: yes.
- Private storage: yes.
- Public sharing: yes.
- Signed links: yes.
- Google Chat: no.
- Notifications: yes.
- Notification name: `e2e-intake`.
- Generate secret: yes.
- Install event listener: yes.
- Brief agent: yes.
- Final confirmation: yes.

Important output:

```text
agent 'e2e-luma' created
agent 'e2e-luma' is not ready for storage config yet; retrying...
Hermes installed on 'e2e-luma'
Hermes model configured on 'e2e-luma'
Deploying Cloud Run receiver 'ghosty-e2e-luma-webhook-e2e-intake'
agent 'e2e-luma' is not ready for notification config yet; retrying...
Installing notification consumer 'ghosty-e2e-intake-consumer.service' on 'e2e-luma'
agent 'e2e-luma' was briefed about models
agent 'e2e-luma' was briefed about storage
agent 'e2e-luma' was briefed about notifications

DONE
Agent created
Private file space
Hermes
Notifications
Notification event listener
Brief agent about models
Brief agent about storage
Brief agent about notifications

Skipped
- Shared internet already enabled
- Models already enabled
- Chat skipped
```

Secret output was redacted from this file. A log scan found no `Needs retry`, `failed`, `Traceback`, `Exception`, `Could not`, `ERROR`, or `✗` markers:

```bash
grep -E "Needs retry|failed|Traceback|Exception|Could not|ERROR|✗" /tmp/ghosty-e2e-final2.log || true
```

Output:

```text
<empty>
```

Bug:

- None in this final clean run.

Skipped:

- Google Chat was skipped intentionally because the Google Chat app still requires manual Google Console / Workspace configuration. This was a guided interview choice, not an execution failure.

CLI proof:

```bash
.venv/bin/ghosty-agents agents
```

Output:

```text
alba-nury RUNNING us-east1-b e2-medium 10.10.0.2
e2e-luma  RUNNING us-east1-b e2-medium 10.10.0.8
```

```bash
.venv/bin/ghosty-agents hermes status e2e-luma
```

Output:

```text
installed YES
command YES
environment YES
config YES
gateway RUNNING
provider vertex
model google/gemini-3.1-pro-preview
Vertex project ghosty-agents
Vertex region global
version Hermes Agent v0.18.0 (2026.7.1) · upstream 1ea0bbbb
```

```bash
.venv/bin/ghosty-agents storage status
```

Output for `e2e-luma`:

```text
PRIVATE YES
PRIVATE IAM YES
PUBLIC YES
PUBLIC IAM YES
PUBLIC READ YES
SIGNED URL YES
LEGACY BROAD NO
VM ENV YES
```

```bash
.venv/bin/ghosty-agents models status
```

Output:

```text
api enabled YES
future-agent auto-grant YES
e2e-luma HAS ROLE YES
```

```bash
.venv/bin/ghosty-agents notifications status e2e-luma --name e2e-intake
```

Output:

```text
URL present
TOPIC YES
SUB YES
RUN YES
PUB/SUB YES
IAM YES
VM ENV YES
CONSUMER RUNNING
Notification URL: https://ghosty-e2e-luma-webhook-e2e-intake-3oahiorpea-ue.a.run.app
Agent subscription: projects/ghosty-agents/subscriptions/e2e-luma-webhook-e2e-intake-events-sub
Agent config path: ~/.config/hermes/webhooks/e2e-intake.env
```

GCP proof:

```bash
gcloud compute instances describe ghosty-e2e-luma \
  --zone=us-east1-b \
  --configuration ghosty-agents \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --format='table(name,status,networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[].natIP,serviceAccounts[0].email)'
```

Output:

```text
NAME             STATUS   NETWORK_IP  NAT_IP  EMAIL
ghosty-e2e-luma  RUNNING  10.10.0.8           ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com
```

```bash
gcloud run services describe ghosty-e2e-luma-webhook-e2e-intake \
  --region=us-east1 \
  --configuration ghosty-agents \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --format='table(status.url,status.conditions.type,status.conditions.status)'
```

Output:

```text
URL                                                                 TYPE                                             STATUS
https://ghosty-e2e-luma-webhook-e2e-intake-3oahiorpea-ue.a.run.app  ['Ready', 'ConfigurationsReady', 'RoutesReady']  ['True', 'True', 'True']
```

```bash
gcloud pubsub subscriptions describe e2e-luma-webhook-e2e-intake-events-sub \
  --configuration ghosty-agents \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --format='table(name,topic,state,pushConfig.pushEndpoint)'
```

Output:

```text
NAME                                                                         TOPIC                                                             STATE   PUSH_ENDPOINT
projects/ghosty-agents/subscriptions/e2e-luma-webhook-e2e-intake-events-sub  projects/ghosty-agents/topics/e2e-luma-webhook-e2e-intake-events  ACTIVE
```

```bash
gcloud compute routers nats describe ghosty-nat \
  --router=ghosty-router \
  --region=us-east1 \
  --configuration ghosty-agents \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --format='table(name,type,natIpAllocateOption,sourceSubnetworkIpRangesToNat,subnetworks[0].sourceIpRangesToNat)'
```

Output:

```text
NAME        TYPE    NAT_IP_ALLOCATE_OPTION  SOURCE_SUBNETWORK_IP_RANGES_TO_NAT  SOURCE_IP_RANGES_TO_NAT
ghosty-nat  PUBLIC  AUTO_ONLY               LIST_OF_SUBNETWORKS                 ['ALL_IP_RANGES']
```

```bash
gcloud projects get-iam-policy ghosty-agents \
  --configuration ghosty-agents \
  --account=daniel@melonn.com \
  --flatten='bindings[].members' \
  --filter='bindings.role=roles/aiplatform.user AND bindings.members:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com' \
  --format='table(bindings.role,bindings.members)'
```

Output:

```text
ROLE                   MEMBERS
roles/aiplatform.user  serviceAccount:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com
```

```bash
gcloud pubsub topics get-iam-policy e2e-luma-webhook-e2e-intake-events \
  --configuration ghosty-agents \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --flatten='bindings[].members' \
  --filter='bindings.members:e2e-luma-e2e-intake-wh-run-sa@ghosty-agents.iam.gserviceaccount.com' \
  --format='table(bindings.role,bindings.members)'
```

Output:

```text
ROLE                    MEMBERS
roles/pubsub.publisher  serviceAccount:e2e-luma-e2e-intake-wh-run-sa@ghosty-agents.iam.gserviceaccount.com
```

```bash
gcloud pubsub subscriptions get-iam-policy e2e-luma-webhook-e2e-intake-events-sub \
  --configuration ghosty-agents \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --flatten='bindings[].members' \
  --filter='bindings.members:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com' \
  --format='table(bindings.role,bindings.members)'
```

Output:

```text
ROLE                     MEMBERS
roles/pubsub.subscriber  serviceAccount:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com
roles/pubsub.viewer      serviceAccount:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com
```

```bash
gcloud storage managed-folders get-iam-policy gs://ghosty-agents-ghosty-agent-storage/agents/e2e-luma/ \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --format='json'
```

Output:

```json
{
  "bindings": [
    {
      "members": [
        "serviceAccount:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com"
      ],
      "role": "roles/storage.objectUser"
    }
  ],
  "etag": "CAE="
}
```

```bash
gcloud storage managed-folders get-iam-policy gs://ghosty-agents-ghosty-agent-public/agents/e2e-luma/ \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --format='json'
```

Output:

```json
{
  "bindings": [
    {
      "members": [
        "serviceAccount:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com"
      ],
      "role": "roles/storage.objectUser"
    },
    {
      "members": [
        "allUsers"
      ],
      "role": "roles/storage.objectViewer"
    }
  ],
  "etag": "CAI="
}
```

```bash
gcloud iam service-accounts get-iam-policy ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com \
  --configuration ghosty-agents \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --flatten='bindings[].members' \
  --filter='bindings.role=roles/iam.serviceAccountTokenCreator AND bindings.members:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com' \
  --format='table(bindings.role,bindings.members)'
```

Output:

```text
ROLE                                  MEMBERS
roles/iam.serviceAccountTokenCreator  serviceAccount:ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com
```

VM proof:

```bash
gcloud compute ssh ghosty-e2e-luma \
  --zone=us-east1-b \
  --tunnel-through-iap \
  --configuration ghosty-agents \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --command='set -eu; grep -E "^(GHOSTY_BUCKET|GHOSTY_BUCKET_URI|GHOSTY_PUBLIC_BUCKET|GHOSTY_PUBLIC_BUCKET_URI|GHOSTY_PUBLIC_BASE_URL|GHOSTY_SIGNING_SERVICE_ACCOUNT|GOOGLE_CLOUD_PROJECT|SIGNED_URLS)=" ~/.config/hermes/storage.env; grep -E "^(GHOSTY_WEBHOOK_NAME|GHOSTY_WEBHOOK_PROVIDER|GHOSTY_WEBHOOK_SUBSCRIPTION|GHOSTY_WEBHOOK_TOPIC|GHOSTY_WEBHOOK_EVENT_FORMAT|GOOGLE_CLOUD_PROJECT|GHOSTY_WEBHOOK_URL)=" ~/.config/hermes/webhooks/e2e-intake.env; systemctl --user is-active hermes-gateway.service; systemctl --user is-active ghosty-e2e-intake-consumer.service' \
  --quiet
```

Output:

```text
GHOSTY_BUCKET=ghosty-agents-ghosty-agent-storage
GHOSTY_BUCKET_URI=gs://ghosty-agents-ghosty-agent-storage/agents/e2e-luma/
GHOSTY_PUBLIC_BUCKET=ghosty-agents-ghosty-agent-public
GHOSTY_PUBLIC_BUCKET_URI=gs://ghosty-agents-ghosty-agent-public/agents/e2e-luma/
GHOSTY_PUBLIC_BASE_URL=https://storage.googleapis.com/ghosty-agents-ghosty-agent-public/agents/e2e-luma/
GHOSTY_SIGNING_SERVICE_ACCOUNT=ghosty-e2e-luma-sa@ghosty-agents.iam.gserviceaccount.com
GOOGLE_CLOUD_PROJECT=ghosty-agents
SIGNED_URLS=enabled
GHOSTY_WEBHOOK_NAME=e2e-intake
GHOSTY_WEBHOOK_PROVIDER=generic
GHOSTY_WEBHOOK_SUBSCRIPTION=projects/ghosty-agents/subscriptions/e2e-luma-webhook-e2e-intake-events-sub
GHOSTY_WEBHOOK_TOPIC=projects/ghosty-agents/topics/e2e-luma-webhook-e2e-intake-events
GHOSTY_WEBHOOK_EVENT_FORMAT=ghosty.webhook.v1
GOOGLE_CLOUD_PROJECT=ghosty-agents
GHOSTY_WEBHOOK_URL=https://ghosty-e2e-luma-webhook-e2e-intake-3oahiorpea-ue.a.run.app
active
active
```

Webhook delivery proof:

```bash
curl -H 'X-Ghosty-Webhook-Secret:<redacted>' \
  -d '{"check":"ghosty-e2e-1783364454","source":"codex-final-proof"}' \
  https://ghosty-e2e-luma-webhook-e2e-intake-3oahiorpea-ue.a.run.app
```

Output:

```text
{"message_id":"20548701938974919","ok":true}
HTTP:200
```

VM event proof:

```bash
gcloud compute ssh ghosty-e2e-luma \
  --zone=us-east1-b \
  --tunnel-through-iap \
  --configuration ghosty-agents \
  --project=ghosty-agents \
  --account=daniel@melonn.com \
  --command="set -eu; file=\$(grep -R -l 'ghosty-e2e-1783364454' ~/.config/hermes/inbox/events/e2e-intake/*.json | tail -1); echo EVENT_FILE=\$file; grep 'ghosty-e2e-1783364454' \$file; echo CONSUMER=\$(systemctl --user is-active ghosty-e2e-intake-consumer.service); log=\${file%.json}.log; echo LOG_FILE=\$log; cat \$log" \
  --quiet
```

Output:

```text
EVENT_FILE=/home/daniel_melonn_com/.config/hermes/inbox/events/e2e-intake/20548701938974919.json
  "body": "{\"check\":\"ghosty-e2e-1783364454\",\"source\":\"codex-final-proof\"}",
    "check": "ghosty-e2e-1783364454",
CONSUMER=active
LOG_FILE=/home/daniel_melonn_com/.config/hermes/inbox/events/e2e-intake/20548701938974919.log
{
  "returncode": 0,
  "status": "completed",
  "stderr": "",
  "stdout": "**Summary:** This event is an automated end-to-end health check or proof-of-life ping (`ghosty-e2e-1783364454`) sent to the `e2e-intake` webhook by the `codex-final-proof` source. \n\n**Action Needed:** No action is needed. This is a standard automated test payload verifying that the webhook and integration are functioning correctly, with no errors or actionable alerts present in the data.\n"
}
```

Final automated test suite:

```bash
.venv/bin/python -m pytest -q
```

Output:

```text
140 passed in 1.41s
```
