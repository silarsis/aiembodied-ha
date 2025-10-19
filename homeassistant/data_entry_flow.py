"""Data entry flow helper types for Home Assistant stub."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Dict


class FlowResultType(StrEnum):
    """Enumeration mirroring Home Assistant's flow result types."""

    FORM = "form"
    CREATE_ENTRY = "create_entry"
    ABORT = "abort"


FlowResult = Dict[str, Any]
