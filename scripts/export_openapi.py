"""Export the Talkies REST contract for documentation and code generation."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import yaml

from talkies.server import app

schema = app.openapi()
with Path("pyproject.toml").open("rb") as project:
    schema["info"]["version"] = tomllib.load(project)["project"]["version"]

# The Python generator picks the first supported media type. A text parser
# also accepts JSON and subtitles without assuming their format.
transcription = schema["paths"]["/v1/audio/transcriptions"]["post"]
content = transcription["responses"]["200"]["content"]
content["application/json"] = content.pop("application/json")

yaml.safe_dump(schema, sys.stdout, sort_keys=False)
