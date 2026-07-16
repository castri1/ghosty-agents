# Ghosty Agents CLI reference

Running `ghosty-agents` with no subcommand opens the interactive control room.
This page documents the individual commands for automation and advanced use.

For installation and the guided workflow, start with the
[beginner README](../README.md). Architecture, security, resource behavior, and
contributor setup are in the [technical reference](technical-reference.md).

## Commands

| Command | Description |
|---|---|
| `ghosty-agents` / `ghosty-agents console` | Open the interactive control room |
| `ghosty-agents init` | Run the setup wizard for account, billing, project, and defaults; use `--non-interactive` for automation |
| `ghosty-agents check` | Check local and cloud readiness; `--quick` skips Google Cloud calls |
| `ghosty-agents prepare` | Prepare the shared project; add `--with-nat` for shared internet |
| `ghosty-agents internet status` / `enable` / `disable` | Manage shared outbound internet |
| `ghosty-agents models status` / `enable` / `disable` | Manage Google AI model access |
| `ghosty-agents storage status` / `add` / `sync` / `disable` | Manage private storage and optional sharing features |
| `ghosty-agents create <name>` | Create an agent; supports `--startup-script`, `--with-hermes`, and `-y` |
| `ghosty-agents agents` | Show the live agent inventory |
| `ghosty-agents details <name>` | Show one agent's details |
| `ghosty-agents connect <name>` | Connect through SSH/IAP; arguments after the name run remotely |
| `ghosty-agents start <name>` / `stop <name>` | Start or stop an agent |
| `ghosty-agents hermes install` / `configure` / `status` / `sync` | Install, configure, and inspect Hermes |
| `ghosty-agents chat status` / `add` / `remove` | Add or manage Google Chat for an agent |
| `ghosty-agents notifications status` / `add` / `sync` / `remove` | Add or manage external notifications |
| `ghosty-agents instruct <name> --service chat\|notifications\|storage\|models` | Brief Hermes about an attached service |
| `ghosty-agents remove <name>` | Remove an agent and its attached per-agent resources |
| `ghosty-agents clean-up` | Remove shared resources; supports `--all-agents` and `--delete-project` |
| `ghosty-agents settings show` / `set <key> <value>` | View or change saved configuration |

## Scripted example

```bash
ghosty-agents init
ghosty-agents check
ghosty-agents prepare --with-nat
ghosty-agents models enable
ghosty-agents storage add
ghosty-agents create worker-1 --with-hermes
ghosty-agents hermes status worker-1
ghosty-agents agents
ghosty-agents connect worker-1
ghosty-agents stop worker-1
ghosty-agents start worker-1
ghosty-agents remove worker-1
```

Optional services can be attached after creation:

```bash
ghosty-agents chat add worker-1 --chat-project ghosty-agent-chat
ghosty-agents notifications add worker-1 --with-consumer
ghosty-agents instruct worker-1 --service notifications --name webhook
```

## Command discovery

Use `--help` at any level to see the available arguments:

```bash
ghosty-agents --help
ghosty-agents create --help
ghosty-agents notifications add --help
```

The older command names remain available for compatibility. See the
[technical reference](technical-reference.md#compatibility-commands) before
maintaining automation that uses them.
