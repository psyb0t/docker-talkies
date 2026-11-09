---
title: "Agent integrations"
description: "Install the Talkies agent skill and plugins for Claude Code, Codex and OpenClaw, and connect MCP clients to /v1/mcp through the stdio bridge."
nav_order: 8
---

# Agent integrations

The [Talkies skill](https://github.com/psyb0t/docker-talkies/tree/main/.agents/skills/talkies)
teaches agents to use the HTTP, WebSocket, and MCP routes of a Talkies server
you run yourself. Install it through the shared `psyb0t` marketplace or let
Codex discover it directly from a checkout of this repository. None of the
integrations ship the speech engine; each one points at your server.

## Claude Code

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install talkies@psyb0t
```

Claude Code prompts for the Talkies URL and, when enabled, the bearer token;
the sensitive token is stored through the client's protected configuration.
The manifest is
[`.agents/.claude-plugin/plugin.json`](https://github.com/psyb0t/docker-talkies/blob/main/.agents/.claude-plugin/plugin.json).

## Codex

```bash
codex plugin marketplace add psyb0t/agents
codex plugin add talkies@psyb0t
```

A marketplace install invokes the skill as `$talkies:talkies`. Codex also
discovers `.agents/skills/talkies` directly in this repository, where it is
invoked as `$talkies` without installation.

## OpenClaw

The skill and the MCP bridge are published through ClawHub:

```bash
openclaw skills install @psyb0t/talkies
openclaw plugins install clawhub:@psyb0t/talkies
```

## MCP bridge

The [OpenClaw plugin](https://github.com/psyb0t/docker-talkies/tree/main/.agents/plugins/talkies)
is a small stdio to HTTP bridge. It runs `mcp-remote` against
`$TALKIES_URL/v1/mcp`, so any MCP client that only speaks local stdio servers
can reach the six Talkies MCP tools listed in [HTTP API](api.md#mcp).

| Variable | Required | Meaning |
|---|---|---|
| `TALKIES_URL` | yes | Base URL of your Talkies server, such as `http://localhost:8000`; the bridge appends `/v1/mcp` |
| `TALKIES_AUTH_TOKEN` | no | Bearer token, needed only when the server sets `TALKIES_AUTH_TOKEN` |

A client that supports remote Streamable HTTP MCP servers does not need the
bridge: point it at `$TALKIES_URL/v1/mcp` with an
`Authorization: Bearer <token>` header. MCP covers ASR and file staging only;
speech synthesis stays on `POST /v1/audio/speech`.
