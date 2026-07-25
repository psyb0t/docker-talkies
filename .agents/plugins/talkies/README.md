# @psyb0t/talkies

An OpenClaw/MCP plugin that connects your agent to a self-hosted
[talkies](https://github.com/psyb0t/docker-talkies) speech API over the
[Model Context Protocol](https://modelcontextprotocol.io).

talkies already serves a Streamable-HTTP MCP endpoint at `/v1/mcp`. This
package is a thin stdio↔HTTP bridge (via
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote)) for MCP clients that
speak local stdio servers — it forwards everything to your running talkies
instance and authenticates with your bearer token when the server requires one.

> talkies is **self-hosted**. This plugin does not ship the speech engine —
> it connects to a talkies server that **you** run. See the
> [talkies repo](https://github.com/psyb0t/docker-talkies) to stand one up.

## Tools

The 6 talkies MCP tools become available to your agent: `list_models`
(discover ASR slugs), `transcribe` (run ASR on a URL or a staged file, with
`response_format` / `language` / `diarization` options), and server-side file
staging — `list_files`, `put_file`, `get_file`, `delete_file`. MCP exposes the
ASR surface only; TTS (`/v1/audio/speech`) is HTTP-only.

## Configuration

| Env var | Required | Description |
|---|---|---|
| `TALKIES_URL` | yes | Base URL of your running talkies server, e.g. `http://localhost:8000`. The bridge appends `/v1/mcp`. |
| `TALKIES_AUTH_TOKEN` | no | Bearer token — only if the talkies server was started with `TALKIES_AUTH_TOKEN` set. |

## Install

Install it into your OpenClaw agent from ClawHub:

```bash
openclaw plugins install clawhub:@psyb0t/talkies
```

Then set `TALKIES_URL` (and `TALKIES_AUTH_TOKEN` if your server uses auth) in
the plugin's environment.

## Native remote MCP (no install)

If your MCP client already supports **remote** Streamable-HTTP servers, you
don't need this bridge — point the client straight at
`$TALKIES_URL/v1/mcp` with an `Authorization: Bearer <token>` header.

## License

MIT. See [LICENSE](LICENSE).
