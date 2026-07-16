# Ghosty Agents

Ghosty Agents helps you create and manage private AI agent machines in Google
Cloud. You do not need to memorize cloud commands: the interactive control room
walks you through project setup, creating an agent, adding capabilities, and
managing costs.

## Before you begin

You need:

- Python 3.11 or newer
- [`pipx`](https://pipx.pypa.io/stable/installation/) for installing the app
- The [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
- A Google Cloud account with access to a billing account

If you still need a billing account, follow the
[billing setup guide](docs/step-0-billing-and-project.md).

After installing the Google Cloud CLI, sign in:

```bash
gcloud auth login
```

## Install Ghosty Agents

Install the latest released version with `pipx`:

```bash
pipx install https://github.com/castri1/ghosty-agents/releases/download/v0.1.0/ghosty_agents-0.1.0-py3-none-any.whl
```

Other installation options:

```bash
# Install the stable v0.1.0 source tag
pipx install "git+https://github.com/castri1/ghosty-agents.git@v0.1.0"

# Install the current development version
pipx install "git+https://github.com/castri1/ghosty-agents.git@main"
```

## Set up Ghosty

Run the friendly setup wizard:

```bash
ghosty-agents init
```

The wizard has three short steps:

1. Choose the Google account Ghosty should use.
2. Choose the billing account dedicated to your agents.
3. Choose a unique Google Cloud project ID for your agent fleet.

You can keep the suggested region, machine size, and budget. Before saving,
Ghosty shows you a summary. It can then check your setup and prepare the project
for you.

## Open the control room

Start the interactive experience with:

```bash
ghosty-agents
```

Use the arrow keys to choose what you want to do:

1. Choose **Prepare/check project**, then **Prepare project** if this is your
   first time. **Check setup** confirms that everything is ready.
2. Choose **Create agent** and enter a name.
3. Choose **Recommended setup** to let Ghosty guide you through Hermes, Google
   AI models, private files, optional Chat and notifications, and internet
   access.
4. Review the setup and cost notes, then confirm when you are ready.
5. Later, choose **Select agent** to open its menu.

From an agent's menu you can:

- **View harness** to see its connected capabilities in your browser
- **Connect** to the agent
- **Start** or **Stop** it
- Add Chat or notifications
- Sync storage
- Install, configure, or check Hermes
- View details or remove the agent

It is safe to exit the control room. Run `ghosty-agents` whenever you want to
open it again; your settings are saved and the live agent inventory comes from
Google Cloud.

## Costs and confirmations

Google Cloud may charge for agent machines, shared internet, model usage, file
storage, and notification services. Ghosty displays warnings and asks for
confirmation before billable setup actions. Stop an agent when you are not using
it, or remove it when you no longer need it.

## Troubleshooting

If `ghosty-agents` is not found after installation, run:

```bash
pipx ensurepath
```

Then close and reopen your terminal.

If Ghosty says that `gcloud` is missing, install the
[Google Cloud CLI](https://cloud.google.com/sdk/docs/install) and run
`gcloud auth login`.

For authentication, billing, project, or API problems, run:

```bash
ghosty-agents check
```

Ghosty prints the failed checks and the action needed to fix each one.

## Advanced documentation

- [Scriptable CLI command reference](docs/cli.md)
- [Technical architecture and contributor guide](docs/technical-reference.md)
- [Billing and project setup](docs/step-0-billing-and-project.md)
- [GitHub installation and release guide](docs/github-distribution.md)
- [Hardened deployment runbook](docs/runbook-deployment.md)

Ghosty Agents is licensed under the [MIT License](LICENSE).
