from dataclasses import dataclass
from typing import Any


@dataclass
class CaseState:
    """Explicit container for the mutable per-case state previously held in module globals."""

    active_subcase_id: int = 0
    df_historical: Any = None
    df_preference: Any = None
    df_transfers: Any = None
    df_newvalue: Any = None
    df_snv: Any = None
    df_ocb: Any = None
    loaded: bool = False
    meta: Any = None
    subcase_by_master: Any = None