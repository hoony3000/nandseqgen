# nandsim_demo_p0.py — P0 통합본 (stdlib-only)
# - Event-jump Scheduler
# - Phase Hook 생성(스케줄러)
# - PolicyEngine with earliest_start + precheck
# - Obligation (READ -> DOUT) with plane/time filtering
# - AddressManager: state snapshot(now), precheck/reserve with multi-plane
# - 간단 통계: propose calls / scheduled ops

from __future__ import annotations
import heapq, random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple, Set

# ---------------------------- Config ----------------------------

CFG = {
    "rng_seed": 12345,
    "policy": {
        "queue_refill_period_us": 3.0,
        "lookahead_k": 4,
        "run_until_us": 150.0,
    },
    "weights": {
        "base": {  # class=host only for demo
            "host": {
                "READ": 0.85,
                "PROGRAM": 0.10,
                "ERASE": 0.05,
                "SR": 0.00,
                "RESET": 0.00,
                "DOUT": 0.00,  # obligation-driven only
            }
        },
        "g_state": {  # very light demo weights
            "pgmable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.8},
            "readable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.9},
        },
        "g_local": {"plane_busy_frac": {"low": 1.2, "mid": 1.0, "high": 0.9}},
        "g_phase": {
            "READ": {"START_NEAR": 1.2, "MID_NEAR": 0.9, "END_NEAR": 1.2},
            "PROGRAM": {"START_NEAR": 1.1, "MID_NEAR": 1.0, "END_NEAR": 0.9},
            "ERASE": {"START_NEAR": 0.9, "MID_NEAR": 1.1, "END_NEAR": 1.1},
            "SR": {"START_NEAR": 1.0, "MID_NEAR": 1.0, "END_NEAR": 1.0},
            "RESET": {"START_NEAR": 1.0, "MID_NEAR": 1.0, "END_NEAR": 1.0},
        },
    },
    "mixture": {"classes": {"host": 1.0}},  # host-only for demo
    "op_specs": {
        "READ": {
            "states": [
                {"name": "ISSUE", "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "dist": {"kind": "normal", "mean": 8.0, "std": 1.5, "min": 2.0}},
                {"name": "DATA_OUT", "dist": {"kind": "normal", "mean": 2.0, "std": 0.4, "min": 0.5}},
            ],
            "hooks": [
                {"when": "STATE_START", "states": ["ISSUE", "CORE_BUSY", "DATA_OUT"], "jitter_us": 0.1},
                {"when": "STATE_MID", "states": ["CORE_BUSY"], "jitter_us": 0.2},
                {"when": "STATE_END", "states": ["DATA_OUT"], "jitter_us": 0.1},
            ],
            "phase_offset_us": {"default": 0.0},
        },
        "DOUT": {
            "states": [
                {"name": "ISSUE", "dist": {"kind": "fixed", "value": 0.2}},
                {"name": "DATA_OUT", "dist": {"kind": "normal", "mean": 1.0, "std": 0.2, "min": 0.2}},
            ],
            "hooks": [
                {"when": "STATE_START", "states": ["DATA_OUT"], "jitter_us": 0.05},
                {"when": "STATE_END", "states": ["DATA_OUT"], "jitter_us": 0.05},
            ],
            "phase_offset_us": {"default": 0.0},
        },
        "PROGRAM": {
            "states": [
                {"name": "ISSUE", "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "dist": {"kind": "normal", "mean": 20.0, "std": 3.0, "min": 8.0}},
            ],
            "hooks": [{"when": "STATE_END", "states": ["CORE_BUSY"], "jitter_us": 0.2}],
            "phase_offset_us": {"default": 0.0},
        },
        "ERASE": {
            "states": [
                {"name": "ISSUE", "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "dist": {"kind": "normal", "mean": 40.0, "std": 5.0, "min": 15.0}},
            ],
            "hooks": [{"when": "STATE_END", "states": ["CORE_BUSY"], "jitter_us": 0.2}],
            "phase_offset_us": {"default": 0.0},
        },
    },
    "obligations": [
        {
            "issuer": "READ",
            "require": "DOUT",
            "window_us": {"kind": "normal", "mean": 6.0, "std": 1.5, "min": 1.0},
            "priority_boost": {"start_us_before_deadline": 2.5, "boost_factor": 2.0, "hard_slot": True},
        }
    ],
    "priority": {
        "order": ["HOST", "READ", "DOUT", "ERASE", "PROGRAM_NEW", "PROGRAM_TARGET", "SR", "RESET"],
        "starvation_aging_alpha": 0.01,
    },
    "topology": {"dies": 1, "planes_per_die": 2, "blocks_per_plane": 4, "pages_per_block": 16},
}

# ---------------------------- Models ----------------------------

class OpKind(Enum):
    READ=auto(); DOUT=auto(); PROGRAM=auto(); ERASE=auto(); SR=auto(); RESET=auto()

@dataclass(frozen=True)
class Address:
    die:int; plane:int; block:int; page:Optional[int]=None

@dataclass
class PhaseHook:
    time_us: float
    label: str
    die:int; plane:int

@dataclass
class StateSeg:
    name:str
    dur_us: float

@dataclass
class Operation:
    kind: OpKind
    targets: List[Address]
    states: List[StateSeg]
    movable: bool = True
    meta: Dict[str,Any] = field(default_factory=dict)

@dataclass
class BusySlot:
    start_us: float
    end_us: float
    op: Operation

def _addr_str(a: Address)->str:
    return f"(d{a.die},p{a.plane},b{a.block},pg{a.page})"

# ---------------------------- Utility: Distributions ----------------------------

def sample_dist(d: Dict[str, Any]) -> float:
    k = d["kind"]
    if k == "fixed":
        return float(d["value"])
    if k == "normal":
        m, s = d["mean"], d["std"]
        mn = d.get("min", 0.0)
        v = random.gauss(m, s)
        return max(v, mn)
    if k == "exp":
        lam = d["lambda"]
        return random.expovariate(lam)
    raise ValueError(f"unknown dist kind: {k}")

def rand_jitter(ampl_us: float) -> float:
    if ampl_us<=0: return 0.0
    return random.uniform(-ampl_us, ampl_us)

# ---------------------------- OpSpec: build ops & hooks ----------------------------

def build_operation(kind: OpKind, cfg_op: Dict[str, Any], targets: List[Address]) -> Operation:
    states=[]
    for s in cfg_op["states"]:
        dur = sample_dist(s["dist"])
        states.append(StateSeg(name=s["name"], dur_us=dur))
    return Operation(kind=kind, targets=targets, states=states)

def make_phase_hooks(op: Operation, start_us: float, cfg_op: Dict[str, Any], die:int, plane:int) -> List[PhaseHook]:
    hooks_cfg = cfg_op.get("hooks", [])
    offset = cfg_op.get("phase_offset_us",{}).get("default",0.0)
    base_t = start_us + offset
    hooks=[]
    cur = base_t
    for seg in op.states:
        seg_start = cur
        seg_mid   = cur + seg.dur_us*0.5
        seg_end   = cur + seg.dur_us
        for rule in hooks_cfg:
            if seg.name not in rule["states"]:
                continue
            jitter = rule.get("jitter_us", 0.0)
            if rule["when"]=="STATE_START":
                hooks.append(PhaseHook(time_us=seg_start + rand_jitter(jitter), label=f"{op.kind.name}.{seg.name}.START", die=die, plane=plane))
            elif rule["when"]=="STATE_MID":
                hooks.append(PhaseHook(time_us=seg_mid   + rand_jitter(jitter), label=f"{op.kind.name}.{seg.name}.MID",   die=die, plane=plane))
            elif rule["when"]=="STATE_END":
                hooks.append(PhaseHook(time_us=seg_end   + rand_jitter(jitter), label=f"{op.kind.name}.{seg.name}.END",   die=die, plane=plane))
        cur = seg_end
    return hooks

# ---------------------------- Address Manager ----------------------------

class AddressManager:
    def __init__(self, cfg: Dict[str, Any]):
        topo = cfg["topology"]
        self.dies = topo["dies"]
        self.planes = topo["planes_per_die"]
        # plane availability absolute times
        self.available: Dict[Tuple[int,int], float] = {(0,p): 0.0 for p in range(self.planes)}
        # rotating cursors per plane
        self.cursors: Dict[Tuple[int,int], List[int]] = {(0,p): [0,0,0] for p in range(self.planes)}  # [block, next_pgm_page, next_rd_page]
        self.pages_per_block = topo["pages_per_block"]
        self.blocks_per_plane = topo["blocks_per_plane"]
        # simplistic programmed set per plane
        self.programmed: Dict[Tuple[int,int], Set[Tuple[int,int]]] = {(0,p): set() for p in range(self.planes)}
        # reservation table per plane: list of (start,end,block)
        self.resv: Dict[Tuple[int,int], List[Tuple[float,float,Optional[int]]]] = {(0,p): [] for p in range(self.planes)}

    # ---- observation / selection / checks ----

    def available_at(self, die:int, plane:int) -> float:
        return self.available[(die,plane)]

    def observe_states(self, die:int, plane:int, now_us: float):
        prog = len(self.programmed[(die,plane)])
        # naive buckets for demo
        pgmable_ratio  = "mid" if prog < 10 else "low"
        readable_ratio = "mid" if prog > 0 else "low"
        plane_busy_frac = "low"  # (could be derived from resv overlap with [now,now+Δ])
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls": "host"},
                {"plane_busy_frac": plane_busy_frac})

    def select(self, kind: OpKind, die:int, plane:int) -> List[Address]:
        if kind == OpKind.READ:
            prog = sorted(self.programmed[(die,plane)])
            tgt = prog[0] if prog else (0,0)
            return [Address(die, plane, block=tgt[0], page=tgt[1])]
        if kind == OpKind.DOUT:
            raise RuntimeError("DOUT selection must be provided by obligation targets")
        if kind == OpKind.PROGRAM:
            b, pgm_p, _ = self.cursors[(die,plane)]
            addr = Address(die, plane, block=b, page=pgm_p)
            # advance cursor
            pgm_p += 1
            if pgm_p >= self.pages_per_block:
                pgm_p = 0
                b = (b+1) % self.blocks_per_plane
            self.cursors[(die,plane)] = [b, pgm_p, self.cursors[(die,plane)][2]]
            return [addr]
        if kind == OpKind.ERASE:
            b = self.cursors[(die,plane)][0]
            return [Address(die, plane, block=b, page=None)]
        return [Address(die, plane, block=0, page=0)]

    def precheck(self, kind: OpKind, targets: List[Address], start_hint: float) -> bool:
        """Minimal feasibility check: time overlap + simple block rule."""
        MIN_GUARD = 0.0
        end_hint = start_hint + MIN_GUARD
        # 1) time overlap across all planes in targets
        seen_planes = set()
        for t in targets:
            key = (t.die, t.plane)
            if key in seen_planes: 
                continue
            seen_planes.add(key)
            for (s,e,_) in self.resv[key]:
                if not (end_hint <= s or e <= start_hint):
                    return False
        # 2) simple block rule (placeholder for real dep rules)
        #    forbid ERASE on a block if it's been programmed recently (not tracked here) — skip for demo
        return True

    # ---- reserve / commit ----

    def reserve(self, die:int, plane:int, start:float, end:float, block: Optional[int]=None):
        self.available[(die,plane)] = max(self.available[(die,plane)], end)
        self.resv[(die,plane)].append((start, end, block))

    def commit(self, op: Operation):
        t = op.targets[0]
        key = (t.die, t.plane)
        if op.kind == OpKind.PROGRAM and t.page is not None:
            self.programmed[key].add((t.block, t.page))
        elif op.kind == OpKind.ERASE:
            # erase whole block
            self.programmed[key] = {pp for pp in self.programmed[key] if pp[0] != t.block}

# ---------------------------- Obligation Manager ----------------------------

@dataclass(order=True)
class _ObHeapItem:
    deadline_us: float
    seq: int
    ob: "Obligation" = field(compare=False)

@dataclass
class Obligation:
    require: OpKind
    targets: List[Address]
    deadline_us: float
    boost_factor: float
    hard_slot: bool

class ObligationManager:
    def __init__(self, cfg_list: List[Dict[str,Any]]):
        self.specs = cfg_list
        self.heap: List[_ObHeapItem] = []
        self._seq = 0

    def on_commit(self, op: Operation, now_us: float):
        for spec in self.specs:
            if spec["issuer"] == op.kind.name:
                dt = sample_dist(spec["window_us"])
                ob = Obligation(
                    require = OpKind[spec["require"]],
                    targets = op.targets,
                    deadline_us = now_us + dt,
                    boost_factor = spec["priority_boost"]["boost_factor"],
                    hard_slot = spec["priority_boost"].get("hard_slot", False),
                )
                heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._seq, ob=ob))
                self._seq += 1
                print(f"[{now_us:7.2f} us] OBLIG  created: {op.kind.name} -> {ob.require.name} by {ob.deadline_us:7.2f} us, target={_addr_str(ob.targets[0])}")

    def pop_urgent(self, now_us: float, die:int, plane:int,
                   horizon_us: float, earliest_start: float) -> Optional[Obligation]:
        """Return earliest-deadline obligation for this (die,plane) that we still can start by earliest_start."""
        if not self.heap: 
            return None
        kept: List[_ObHeapItem] = []
        chosen: Optional[Obligation] = None
        while self.heap and not chosen:
            item = heapq.heappop(self.heap)
            ob = item.ob
            tgt = ob.targets[0]
            same_plane = (tgt.die == die and tgt.plane == plane)
            in_horizon = ((ob.deadline_us - now_us) <= max(horizon_us, 0.0))
            feasible_time = (earliest_start <= ob.deadline_us)  # coarse feasibility
            if same_plane and in_horizon and feasible_time:
                chosen = ob
                break
            kept.append(item)
        for it in kept:
            heapq.heappush(self.heap, it)
        return chosen

# ---------------------------- Policy Engine ----------------------------

class PolicyEngine:
    def __init__(self, cfg, addr_mgr: AddressManager, obl_mgr: ObligationManager):
        self.cfg = cfg
        self.addr = addr_mgr
        self.obl = obl_mgr

    def _score(self, op_name: str, phase_label: str, global_state: Dict[str,str], local_state: Dict[str,str]) -> float:
        w = self.cfg["weights"]["base"]["host"].get(op_name, 0.0)
        w *= self.cfg["weights"]["g_state"]["pgmable_ratio"].get(global_state["pgmable_ratio"], 1.0)
        w *= self.cfg["weights"]["g_state"]["readable_ratio"].get(global_state["readable_ratio"], 1.0)
        w *= self.cfg["weights"]["g_local"]["plane_busy_frac"].get(local_state["plane_busy_frac"], 1.0)
        near = "MID_NEAR"
        if phase_label.endswith("START"): near="START_NEAR"
        elif phase_label.endswith("END"): near="END_NEAR"
        w *= self.cfg["weights"]["g_phase"].get(op_name, {}).get(near, 1.0)
        return w

    def propose(self, now_us: float, hook: PhaseHook,
                global_state: Dict[str,str], local_state: Dict[str,str],
                earliest_start: float) -> Optional[Operation]:
        # 0) Obligation first (plane/time filtered)
        ob = self.obl.pop_urgent(now_us, hook.die, hook.plane,
                                 horizon_us=10.0, earliest_start=earliest_start)
        if ob:
            if self.addr.precheck(ob.require, ob.targets, start_hint=earliest_start):
                cfg_op = self.cfg["op_specs"][ob.require.name]
                op = build_operation(ob.require, cfg_op, ob.targets)
                op.meta["source"]="obligation"
                return op
            # else: could attempt alternative targets (omitted in demo)

        # 1) Policy path: weighted pick under phase/state
        cand = []
        for name in ["READ", "PROGRAM", "ERASE"]:
            s = self._score(name, hook.label, global_state, local_state)
            if s>0: cand.append((name, s))
        if not cand: return None
        total = sum(s for _,s in cand)
        r = random.random()*total
        acc=0.0
        pick = cand[-1][0]
        for name, s in cand:
            acc += s
            if r <= acc:
                pick=name; break

        kind = OpKind[pick]
        targets = self.addr.select(kind, hook.die, hook.plane)  # P1: multi-plane/fanout 확장 가능
        if not targets:
            return None
        if not self.addr.precheck(kind, targets, start_hint=earliest_start):
            return None
        op = build_operation(kind, self.cfg["op_specs"][pick], targets)
        op.meta["source"]="policy"
        return op

# ---------------------------- Scheduler ----------------------------

class Scheduler:
    def __init__(self, cfg, addr_mgr: AddressManager, policy_engine: PolicyEngine, obl_mgr: ObligationManager):
        self.cfg=cfg; self.addr=addr_mgr; self.SPE=policy_engine; self.obl=obl_mgr
        self.now=0.0
        self.ev=[]  # (time, seq, type, payload)
        self._seq=0
        self.stat_propose_calls = 0
        self.stat_scheduled = 0
        self._push(0.0, "QUEUE_REFILL", None)
        # Bootstrap hooks per plane
        for plane in range(self.addr.planes):
            self._push(0.0, "PHASE_HOOK", PhaseHook(0.0, "BOOT.START", 0, plane))

    def _push(self, t: float, typ: str, payload: Any):
        heapq.heappush(self.ev, (t, self._seq, typ, payload)); self._seq+=1

    def _unique_plane_keys(self, targets: List[Address]) -> List[Tuple[int,int,int]]:
        # (die, plane, block) tuples deduped
        s=set()
        for a in targets:
            s.add((a.die, a.plane, a.block))
        return list(s)

    def _start_time_for_targets(self, targets: List[Address]) -> float:
        # start cannot be earlier than any involved plane's availability
        planes = {(a.die, a.plane) for a in targets}
        avail = [self.addr.available_at(d,p) for (d,p) in planes]
        return max([self.now] + avail)

    def _schedule_operation(self, op: Operation):
        start = self._start_time_for_targets(op.targets)
        dur = sum(seg.dur_us for seg in op.states)
        end  = start + dur
        # reserve for all involved planes
        for d,p,b in self._unique_plane_keys(op.targets):
            self.addr.reserve(d, p, start, end, block=b)
        # events
        self._push(start, "OP_START", op)
        self._push(end,   "OP_END",   op)
        # hooks for each involved plane
        for d,p,_ in self._unique_plane_keys(op.targets):
            hooks = make_phase_hooks(op, start, self.cfg["op_specs"][op.kind.name], d, p)
            for h in hooks:
                self._push(h.time_us, "PHASE_HOOK", h)
        first = op.targets[0]
        print(f"[{self.now:7.2f} us] SCHED  {op.kind.name:7s} on targets={len(op.targets)} start={start:7.2f} end={end:7.2f} 1st={_addr_str(first)} src={op.meta.get('source')}")
        self.stat_scheduled += 1

    def run_until(self, t_end: float):
        while self.ev and self.ev[0][0] <= t_end:
            self.now, _, typ, payload = heapq.heappop(self.ev)
            if typ=="QUEUE_REFILL":
                # seed hooks to keep flow
                for plane in range(self.addr.planes):
                    self._push(self.now, "PHASE_HOOK", PhaseHook(self.now, "REFILL.NUDGE", 0, plane))
                nxt = self.now + self.cfg["policy"]["queue_refill_period_us"]
                self._push(nxt, "QUEUE_REFILL", None)

            elif typ=="PHASE_HOOK":
                hook: PhaseHook = payload
                earliest_start = self.addr.available_at(hook.die, hook.plane)
                global_state, local_state = self.addr.observe_states(hook.die, hook.plane, self.now)
                self.stat_propose_calls += 1
                op = self.SPE.propose(self.now, hook, global_state, local_state, earliest_start)
                if op:
                    self._schedule_operation(op)
                else:
                    # no viable op at this hook; that's fine
                    pass

            elif typ=="OP_START":
                op: Operation = payload
                first = op.targets[0]
                print(f"[{self.now:7.2f} us] START  {op.kind.name:7s} target={_addr_str(first)}")

            elif typ=="OP_END":
                op: Operation = payload
                first = op.targets[0]
                print(f"[{self.now:7.2f} us] END    {op.kind.name:7s} target={_addr_str(first)}")
                # commit state first
                self.addr.commit(op)
                # then obligations
                self.obl.on_commit(op, self.now)

        # summary
        print(f"\n=== Stats ===")
        print(f"propose calls : {self.stat_propose_calls}")
        print(f"scheduled ops : {self.stat_scheduled}")
        if self.stat_propose_calls:
            rate = 100.0 * self.stat_scheduled / self.stat_propose_calls
            print(f"accept ratio  : {rate:.1f}%")

# ---------------------------- Main ----------------------------

def main():
    random.seed(CFG["rng_seed"])
    addr = AddressManager(CFG)
    obl  = ObligationManager(CFG["obligations"])
    spe  = PolicyEngine(CFG, addr, obl)
    sch  = Scheduler(CFG, addr, spe, obl)
    run_until = CFG["policy"]["run_until_us"]
    print("=== NAND Sequence Generator Demo (P0 Patched) ===")
    sch.run_until(run_until)
    print("=== Done ===")

if __name__ == "__main__":
    main()