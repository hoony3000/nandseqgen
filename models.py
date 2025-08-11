# models.py
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple

class OpKind(Enum):
    READ=auto(); DOUT=auto(); PROGRAM=auto(); ERASE=auto(); SR=auto(); RESET=auto()

@dataclass(frozen=True)
class Address:
    die:int; plane:int; block:int; page:Optional[int]=None

@dataclass
class PhaseHook:
    time_us: float
    label: str            # e.g., "READ.CORE_BUSY.START"
    die:int; plane:int

@dataclass
class StateSeg:
    name:str
    dur_us: float         # 샘플된 상대 duration

@dataclass
class Operation:
    kind: OpKind
    targets: List[Address]
    states: List[StateSeg]   # ISSUE → ... → END
    movable: bool = True
    meta: Dict[str,Any] = field(default_factory=dict)

@dataclass
class BusySlot:
    start_us: float
    end_us: float
    op: Operation