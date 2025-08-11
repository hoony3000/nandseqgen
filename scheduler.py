# scheduler.py (이벤트 점프형, 훅 생성자이자 최종 승인자)
import heapq
from typing import Optional
from models import Operation, BusySlot, PhaseHook, OpKind
from op_spec import make_phase_hooks

class Scheduler:
    def __init__(self, cfg, addr_mgr, policy_engine):
        self.cfg=cfg; self.addr=addr_mgr; self.SPE=policy_engine
        self.now=0.0
        self.ev=[] # (time, type, payload)
        heapq.heappush(self.ev, (0.0, "QUEUE_REFILL", None))

    def _schedule_operation(self, op, die, plane):
        # plane availability를 고려해 start/end 절대 시각 산출(간이)
        start = max(self.now, self.addr.available_at(die,plane))
        dur = sum(seg.dur_us for seg in op.states)
        end  = start + dur
        self.addr.reserve(op.targets)  # 토큰 생략(스켈레톤)
        heapq.heappush(self.ev, (start, "OP_START", (op, die, plane)))
        heapq.heappush(self.ev, (end,   "OP_END",   (op, die, plane)))
        # PHASE_HOOK 생성 (스케줄러 책임)
        hooks = make_phase_hooks(op, start, self.cfg["op_specs"][op.kind.name], die, plane)
        for h in hooks:
            heapq.heappush(self.ev, (h.time_us, "PHASE_HOOK", h))

    def run_until(self, t_end):
        while self.ev and self.ev[0][0] <= t_end:
            self.now, typ, payload = heapq.heappop(self.ev)
            if typ=="QUEUE_REFILL":
                heapq.heappush(self.ev, (self.now + self.cfg["policy"]["queue_refill_period_us"], "QUEUE_REFILL", None))
            elif typ=="PHASE_HOOK":
                hook=payload
                global_state, local_state = self.addr.observe_states(hook.die, hook.plane)
                cand = self.SPE.propose(self.now, hook, global_state, local_state)
                if cand:
                    self._schedule_operation(cand, hook.die, hook.plane)
            elif typ=="OP_START":
                op, die, plane = payload
                # busy 시작: plane 타임라인 기록(생략)
                pass
            elif typ=="OP_END":
                op, die, plane = payload
                # 상태 커밋 + 의무 생성
                self.addr.commit(op.targets, op.kind)
                self.SPE.obl.on_commit(op, self.now)