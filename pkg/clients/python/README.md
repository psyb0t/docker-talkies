# Talkies Python client

Install the typed REST client from this repository.

```sh
uv pip install 'git+https://github.com/psyb0t/docker-talkies.git#subdirectory=pkg/clients/python/talkies-client'
```

Use `Client(base_url="http://localhost:8000")` for a local instance without a token. Use `AuthenticatedClient(base_url=..., token=...)` when Talkies requires bearer authentication.

Speech and file downloads return binary content. For transcription, the client returns the response as text so that JSON, plain text, SRT and VTT all work. Use `json.loads` for JSON formats.

The client is generated from `openapi.yaml`. Run `make generate` from the repository root after changing route metadata and `make generate-check` to verify the result. See the [HTTP guide](https://psyb0t.github.io/docker-talkies/api/) for WebSocket and MCP clients.
