# Step 0 — Dedicated Billing Account & Project (existing GCP account)

> Assumes you **already have a GCP account** but want a **new, separate billing
> account** for Hermes so its costs don't mix with your other projects. This step
> creates that billing account, creates a dedicated project, and links them —
> producing the values `env.sh` needs: `BILLING_ACCOUNT_ID` and `PROJECT_ID`.

> **What's manual vs. scriptable:**
> - **Creating the new billing account is MANUAL (console only).** `gcloud` can list,
>   describe, and link billing accounts, but it **cannot create** a standard
>   (self-serve) Cloud Billing account — that always goes through the console with a
>   payment method.
> - **Project creation and billing-linking are scriptable** — see `scripts/`.

> **No second free trial:** the $300 Welcome credit is once per user. Because your
> Google identity has used GCP before, this new billing account is a **paid** account
> from day one — useful, because an always-on agent shouldn't sit on a trial account
> that auto-closes at 90 days anyway.

---

## 1. Create the new billing account (MANUAL — console only)

You need the **Billing Account Creator** role (`roles/billing.creator`) to do this.
On a personal account you have it by default; under an organization, an org/billing
admin may need to grant it.

1. Go to **https://console.cloud.google.com/billing**.
2. Click **Create account** (or **Add billing account**).
3. Enter a **name** that makes the separation obvious, e.g. `Hermes Agent Billing`.
4. Select **country** and **currency**.
   - ⚠️ Currency is fixed at creation and **cannot be changed later** — pick the right
     one now.
5. Enter a **payment method**. You can reuse your existing Google **payment profile**
   (the saved card/identity behind your other billing accounts) or add a new card.
   - *Billing account* ≠ *payment profile*: one payment profile can back several
     billing accounts. Reusing it is fine and common; the billing accounts still bill
     and report separately.
6. Click **Submit and enable billing**.

You're automatically made **administrator** of the new billing account.

> This gives you exactly the isolation you want: this billing account's invoices,
> budgets, and cost reports cover **only** the Hermes project once you link them in
> step 3.

---

## 2. Find the new Billing Account ID

Helper: `./scripts/bootstrap/list-billing-accounts.sh`

```bash
gcloud billing accounts list
# ACCOUNT_ID            NAME                  OPEN
# 0X0X0X-0X0X0X-0X0X0X  Hermes Agent Billing  True   <- the new one
# 0Y0Y0Y-0Y0Y0Y-0Y0Y0Y  My Existing Billing   True
```

Confirm you copy the **new** account's ID (match it by the NAME you chose), not an
existing one — `OPEN: True` means it's active. Put it in `env.sh`
(`BILLING_ACCOUNT_ID`).

---

## 3. Create a dedicated project for Hermes

A dedicated project pairs naturally with the dedicated billing account: clean
isolation, one-command teardown, and budgets/costs that map 1:1 to this workload.

### Option A — gcloud (scriptable)

Helper: `./scripts/bootstrap/00-create-project.sh` (reads `PROJECT_ID` / `PROJECT_NAME` from `env.sh`)

```bash
# Project ID must be GLOBALLY unique, 6–30 chars, lowercase/digits/hyphens.
gcloud projects create hermes-prod-7f3a --name="Hermes Prod"
gcloud config set project hermes-prod-7f3a
```

### Option B — Console
Project dropdown → **New Project** → name it → **No organization** (personal) →
**Create** → select it.

> Under an **organization** you need `roles/resourcemanager.projectCreator` to create
> projects. Automatic on a personal/no-org account.

Put your chosen Project ID into `env.sh` (`PROJECT_ID`).

---

## 4. Link the NEW billing account to the project

Mandatory — without linked billing, Compute Engine refuses to create the VM. The
whole point of this step is to link the project to the **new** billing account, not
your existing one.

Helper: `./scripts/bootstrap/01-link-billing.sh`

```bash
gcloud billing projects link hermes-prod-7f3a \
  --billing-account=0X0X0X-0X0X0X-0X0X0X   # the NEW account ID from step 2
```

(Console equivalent: open the **new** billing account → **Account management** →
**Link a project** → select the Hermes project.)

> Same command as the runbook's section 2 — do it here and skip the duplicate there.

---

## 5. Verify the link points at the right account

Helper: `./scripts/bootstrap/02-verify-billing.sh`

```bash
gcloud billing projects describe hermes-prod-7f3a
# billingAccountName: billingAccounts/0X0X0X-0X0X0X-0X0X0X   <- must be the NEW id
# billingEnabled: true
```

Double-check `billingAccountName` matches the **new** account. If it shows your old
account, re-run step 4 with the correct ID.

---

## What you now have (feeds into env.sh)

| Value                   | Where you got it                | env.sh variable      |
|-------------------------|---------------------------------|----------------------|
| **New** billing acct ID | Step 2                          | `BILLING_ACCOUNT_ID` |
| Project ID              | Step 3                          | `PROJECT_ID`         |
| Your GCP login email    | the account you're signed in as | `MY_ACCOUNT`         |

Fill those into `env.sh`, run `source env.sh`, then proceed to the hardened
deployment runbook (section 2 onward).

> The runbook's section 2 still runs per-project setup — enabling APIs
> (`gcloud services enable ...`) and the IAP IAM bindings — against this new project.
> Don't skip it.

---

## Cost-safety reminder

Set the **budget alert** (runbook §12) against this **new** billing account right
after linking. Because both the billing account and the project are dedicated to
Hermes, the budget now tracks this one workload precisely — so any unexpected
cost (a hijacked box mining crypto, a runaway agent loop) shows up immediately and
isn't buried under your other projects' spend.
