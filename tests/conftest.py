"""Test setup: make the integration importable without a running Home Assistant."""

import sys
import types
from pathlib import Path

# The bluetooth component pulls in many optional dependencies; the code paths
# under test don't use it, so a stub module is enough for imports.
sys.modules.setdefault(
    "homeassistant.components.bluetooth",
    types.ModuleType("homeassistant.components.bluetooth"),
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components"))
