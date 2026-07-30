# Operations and security

## Network exposure

Talkies binds to `0.0.0.0:8000` inside the container. Prefer a local mapping:

```bash
-p 127.0.0.1:8000:8000
```

For a network-facing deployment, set `TALKIES_AUTH_TOKEN`, terminate TLS at a
reverse proxy or load balancer, and add request-size and rate limits there. The
built-in token is one shared credential, not per-user authorization. It covers
HTTP, WebSocket, and the mounted MCP service; `/healthz` is deliberately open
for health probes.

## Remote files and staged data

`file_path` accepts a staged relative path or an `http(s)` URL. URL downloads
are cached under the files area. File staging is server-wide: there is no caller
ownership model, automatic expiry, or retention policy.

- Mount a dedicated `TALKIES_DATA_DIR` volume with appropriate host access.
- Do not give mutually untrusted clients the same Talkies instance.
- Set `TALKIES_BLOCK_PRIVATE_DOWNLOADS=true` for untrusted URL submitters.
- Delete workflow data with `DELETE /v1/files/{path}` when it is no longer needed.

The file API normalizes leading slashes and rejects traversal, backslashes,
null bytes, and symlink access, but that is not tenant isolation.

## Model lifecycle

Models load lazily. `GET /api/ps` lists loaded models and idle time;
`DELETE /api/ps/{model}` unloads one; `POST /unload` unloads all it can.
`TALKIES_MODEL_TTL=0` disables automatic idle eviction. `TALKIES_PRELOAD`
attempts configured models during startup; preload failures are logged.

An active live-ASR stream pins its model. While pinned, requests that need a
different model and unload endpoints receive HTTP 409. The pin releases on end,
cancel, disconnect, timeout, or error.

## Logs and runtime

`TALKIES_DATA_DIR` contains model snapshots, uploads, URL cache entries, and
custom voices. Treat its mounted volume as application data. Structured logs
go to stdout; debug logging includes speech content. Both images run as the
non-root `talkies` user and expose a health check against `/healthz`. The CUDA
image needs an NVIDIA-compatible host runtime and an explicit GPU assignment.
