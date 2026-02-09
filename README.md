# Kiln

Kiln uses GitHub as your UI to dispatch Claude Code.

You move cards across columns (Backlog -> Research -> Plan -> Implement) and Kiln runs Claude locally, opens PRs, and keeps everything tracked in GitHub.

**Setup instructions**: https://kiln.bot/docs

![Research and Plan demo](https://media.githubusercontent.com/media/agentic-metallurgy/kiln-docs/main/src/assets/ratio-math-research-ready.gif)

---

## Design

It's meant to be simple:

- **Use your existing Claude subscription** (no auth trickery, no API keys needed, runs locally)
- **All context and state is on GitHub** (no markdown mess, no local DBs, easy recovery)
- **Poll instead of webhooks/events** (no external attack surfaces, works behind VPN)
- **Supports MCPs and anything else Claude can do**

That's the heart of it and it works because… it's Claude :)

### The Kanban Board

The control panel—you move Issues around on it and see labels get added/removed to indicate state. [Set up your board](https://kiln.bot/docs/project-board-setup/).

| Backlog | Research | Plan | Implement | Validate | Done |
|-----------|-------------|---------|--------------|-------------|---------|
| *new issues* | *codebase exploration* | *design tasks* | *write code* | *human review* | *complete* |

### Claude CLI as Execution Engine

Executes workflows via the Claude Code CLI:

- **No New Auth Setup**: Leverages existing Claude subscription, no trickery
- **Full Claude capabilities**: `/commands`, MCPs, tools, git operations, local file access

### GitHub as Single Source of Truth

Uses GitHub as the end-to-end context and state store:

- **Single source of truth**: Research and plan outputs are written and iterated on in the issue
- **Crash recovery**: Nothing local is sacred, local DB is cache only
- **Visibility**: End to end context is stored as a single artifact, from PRD -> PR
- **Observability**: Issue to PR to Merge all timestamped, easy to derive analytics
- **No `.md` mess**: No local files to manage and organize, see which prompts made what edits to research and plan

### Polling Over Webhooks

Use periodic polling instead of webhook-based event handling:

- **Security-first**: No external attack surface, public URLs, webhook secrets management
- **Firewall-friendly**: Works behind VPNs without requiring publicly-accessible endpoints

**Trade-off**: 30-second latency (configurable) vs. near-instant webhook response.
