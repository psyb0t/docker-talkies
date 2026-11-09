# MCP rules

Prefix: MCP.

The MCP server is `build_mcp_server()` in `src/talkies/mcp_server.py`, mounted at `/v1/mcp` by `src/talkies/server.py`. The OpenClaw bridge in `.agents/plugins/talkies/` and the MCP Registry entry `server.json` point clients at it.

## Server and transport

- **MCP1:** Keep the FastMCP server stateless with JSON responses (`stateless_http=True`, `json_response=True`) and `streamable_http_path = "/"`, so clients connect to `/v1/mcp` and not `/v1/mcp/mcp`. `_MCPSlashRewriteMiddleware` in `server.py` rewrites bare `/v1/mcp` to `/v1/mcp/` so the mount never answers with a redirect; it is added after `BearerAuthMiddleware`, which makes it the outer layer, and auth sees the rewritten path.
- **MCP2:** `mcp_server.py` never imports `server.py`. Tools that need server state or behaviour get it injected through `build_mcp_server()` (`backends`, `registry`, `transcribe_runner`, `audio_loader`), and use `src/talkies/files.py` for staging. The `transcribe` tool runs `run_transcription_pipeline`, the same function as the HTTP route, so admission, language defaults, VAD and rendering cannot drift between the two. Never reimplement that pipeline in a tool.

## Tools

- **MCP3:** A tool reports failure by raising `ValueError` with a caller-readable message, which FastMCP returns as a tool error. Translate the server's exceptions at the tool boundary: `KeyError` to an unknown-model message, `HTTPException` to its `detail`, `FilePathError` and download errors to their message.
- **MCP4:** File bytes cross MCP as base64 (`put_file` takes `content_base64`, `get_file` returns it), validated with `base64.b64decode(..., validate=True)` and capped at `config.MAX_UPLOAD_BYTES` in both directions, matching `PUT /v1/files/{path}`.
- **MCP5:** The tool docstring is the description agents read. Document every argument, the accepted values, the return shape and any cost (see `transcribe` and `get_file`).
- **MCP6:** MCP covers ASR and file staging only; TTS stays on HTTP, and `list_models` filters out non-ASR backends. The tool count is written out in several places: the `_log.info("mcp server initialised: 6 tools")` line and module docstring in `mcp_server.py`, the `description` in `.agents/plugins/talkies/openclaw.plugin.json`, the Tools section of `.agents/plugins/talkies/README.md`, the skill's description and MCP section in `.agents/skills/talkies/SKILL.md`, and the MCP section of `docs/api.md`. Adding, removing or renaming a tool updates all of them in the same change.

## Bridge plugin

- **MCP7:** `.agents/plugins/talkies/bin/cli.js` is only a launcher for `mcp-remote` (pinned to an exact version in `package.json`) against `$TALKIES_URL/v1/mcp`. stdout is the MCP channel, so the bridge writes diagnostics to stderr only, and it never logs `TALKIES_AUTH_TOKEN`.
