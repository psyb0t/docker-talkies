"""talkies — unified ASR wrapper for aigate (whisper + parakeet + canary).

Pre-poisons sys.modules with a minimal IPython stub before NeMo's import
chain can trigger the real one. NeMo's
nemo.collections.asr.parts.utils.vad_utils does `import IPython.display`
purely for Jupyter audio-playback helpers we never use. The real import
transitively loads IPython.utils.PyColorize, which builds a pygments Theme
using ANSI color names ('ansibrightred', etc.) added in modern IPython.
Under the right sys.modules state — once wandb has loaded, its vendored
ancient pygments fork wins MRO lookup for the Style class — colorformat()
asserts on the ANSI color name → AssertionError tears down the request.

Stubbing IPython here, before any NeMo import, skips the entire chain.
"""

import sys as _sys
import types as _types
from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("talkies")
except _PackageNotFoundError:
    __version__ = "0.0.0+source"


def _install_ipython_stub() -> None:
    if "IPython" in _sys.modules:
        return
    pkg = _types.ModuleType("IPython")
    pkg.__path__ = []  # mark as a package so submodule lookups succeed
    display = _types.ModuleType("IPython.display")

    def _noop(*_args, **_kwargs):
        return None

    for attr in ("Audio", "display", "HTML", "Image", "Markdown", "clear_output"):
        setattr(display, attr, _noop)
    _sys.modules["IPython"] = pkg
    _sys.modules["IPython.display"] = display


_install_ipython_stub()
