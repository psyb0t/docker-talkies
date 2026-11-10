# Docs and release rules

Prefix: DOC.

## Docs move with the code

- **DOC1:** `README.md` stays a short entry point that links to `docs/` (`docs/development.md`: "Keep the root README short and place detailed user documentation in `docs/`"). Put detail in the owning doc, and add a row to the README's Documentation table when a doc file is added. README links to docs use the full published URL (`https://psyb0t.github.io/docker-talkies/<page>/`), because the README is also shown on Docker Hub, where relative links break.
- **DOC2:** A change that alters behaviour a user or agent can see updates the owning docs in the same change:
  - models, executors, registry fields: `docs/models.md` (including its executor list), the README "Models at a glance" section, and the slug lists and counts in `.agents/skills/talkies/SKILL.md` (its frontmatter `description` too);
  - settings: `docs/configuration.md` and `.agents/skills/talkies/references/setup.md`;
  - HTTP routes, files, lifecycle, MCP: `docs/api.md`, and the README "What it provides" table for a new surface;
  - live ASR protocol and PCM TTS streaming: `docs/streaming.md`;
  - request flow, backend selection, on-disk layout: `docs/architecture.md`;
  - exposure, memory, retention, logs: `docs/operations.md`;
  - Make targets and test suites: `docs/development.md` and the README Development block.

  v0.15.3 was a docs-only release because `docs/models.md` and the skill had fallen behind the Chatterbox executor; check every owner above, not only the closest one.
- **DOC15:** `docs/` is a GitHub Pages site (Just the Docs, built from `main` `/docs`); pages carry a `nav_order`. Run `make docs` after a docs change and check the output in `.testing/docs-site`.
- **DOC5:** Prose in `README.md` and `docs/` is hard-wrapped near 80 columns, not one paragraph per line; keep that form in edited files. Settings, routes and close codes go in tables, as `docs/configuration.md` and `docs/streaming.md` do.

## CHANGELOG

- **DOC7:** In `CHANGELOG.md`, a release section goes at the top, below the intro: the heading `## vX.Y.Z`, a space, an em dash, a space, and the date as `YYYY-MM-DD` (matching every existing heading), then one summary sentence on its own line, then `### Added`, `### Changed` and `### Fixed` as needed. A docs-only release uses the summary `Documentation. No code changed.` and lists its bullets directly (v0.15.3).
- **DOC8:** Mark a breaking change with **Breaking.**, as the CHANGELOG intro requires.

## Versions, tags and publishing

- **DOC10:** `pyproject.toml` `version` is the canonical version: `make version` prints it and `make build*` tags images `v<version>` from it. A release sets the same version in `.agents/.codex-plugin/plugin.json` in the same commit, including a docs-only release (v0.16.0 fixed a `pyproject.toml` left behind by v0.15.3). `.agents/.claude-plugin/plugin.json` has no version. `server.json`, `.agents/plugins/talkies/package.json` and `.agents/plugins/talkies/openclaw.plugin.json` stay at `0.0.0` in git; the MCP Registry publish workflow writes the tag's version into `server.json` at publish time.
- **DOC11:** Each release is an annotated tag `vX.Y.Z` on the release commit. The tag message's first line is exactly `vX.Y.Z`, then a blank line, then that release's CHANGELOG section without its `##` heading and `###` subheadings: the summary sentence, a blank line, then the bullets. GitHub titles a release with the tag subject, so the version line keeps the title clean. Tags up to v0.17.0 start with the summary sentence instead.
- **DOC12:** Pushing a `v*` tag runs `.github/workflows/pipeline.yml`: it builds, scans and pushes both images and creates the GitHub release, then publishes the skill and plugin to ClawHub and `server.json` to the MCP Registry. The registry rejects a `server.json` `description` over 100 characters only after the images are out, so check it before tagging.
