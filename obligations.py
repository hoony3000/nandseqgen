# obligations.py
from dataclasses import dataclass
from typing import List, Optional
from models import OpKind, Address, Operation
from dist import sample_dist
import heapq, time

@dataclass
class Obligation:
    require: OpKind
    targets: List[Address]
    deadline_us: float
    boost_factor: float
    hard_slot: bool

class ObligationManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self._heap=[]  # (deadline, idx, Obligation)
        self._ctr=0

    def on_commit(self, op: Operation, now_us: float):
        for spec in self.cfg:
            if spec["issuer"] == op.kind.name:
                dt = sample_dist(spec["window_us"])
                ob = Obligation(
                    require=OpKind[spec["require"]],
                    targets=op.targets,
                    deadline_us= now_us + dt,
                    boost_factor= spec["priority_boost"]["boost_factor"],
                    hard_slot= spec["priority_boost"].get("hard_slot", False)
                )
                heapq.heappush(self._heap, (ob.deadline_us, self._ctr, ob)); self._ctr+=1

    def urgent(self, now_us: float, horizon_us: float=10.0)->List[Obligation]:
        # 마감 임박 의무를 앞쪽에서 몇 개만 꺼냄(조회용)
        res=[]
        for deadline, _, ob in self._heap:
            if deadline - now_us <= horizon_us:
                res.append(ob)
        return res