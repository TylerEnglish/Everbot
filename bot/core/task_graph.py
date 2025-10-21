from __future__ import annotations
from typing import Sequence, Union
from bot.core.phases import Phase

PHASE_ORDER = ["RESEARCH", "DISTILL", "PRACTICE", "EVAL", "DECIDE"]

def _to_name(p: Union[Phase, str]) -> str:
    return p.value if isinstance(p, Phase) else str(p)

def validate_path(phases: Sequence[Union[Phase, str]]) -> bool:
    return list(map(_to_name, phases)) == PHASE_ORDER
