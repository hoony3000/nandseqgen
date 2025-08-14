# nandsim_p2_alias_mplane.py
# - Alias-based multi-plane ops (SIN_*/MUL_* → base op + arity)
# - Non-greedy multi-plane planner with degrade
# - DIE_WIDE CORE_BUSY for PROGRAM/ERASE
# - Global BusResource gating for bus=true state segments
# - Phase-conditional policy + backoff score policy
# - Quantized event time, obligation READ→DOUT
# Stdlib only.

from __future__ import annotations
import heapq, random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple, Set

# ---------------------------- Simulation Resolution ----------------------------
SIM_RES_US = 0.01
def quantize(t: float) -> float:
    return round(t / SIM_RES_US) * SIM_RES_US

# ---------------------------- Config ----------------------------
CFG = {
    "rng_seed": 12345,
    "policy": {
        "queue_refill_period_us": 3.0,
        "run_until_us": 180.0,
        "planner_max_tries": 8,       # 비탐욕 시도 횟수
    },
    # phase-conditional: 훅(OP.STATE(.POS)) → 다음 op 분포(에일리어스 키 허용)
    "phase_conditional": {
        "READ.ISSUE":                {"MUL_PROGRAM": 0.20, "SIN_PROGRAM": 0.10, "SIN_READ": 0.35, "MUL_READ": 0.25, "SIN_ERASE": 0.10},
        "READ.CORE_BUSY.START":      {"MUL_READ": 0.60, "SIN_READ": 0.20, "SIN_PROGRAM": 0.10, "SIN_ERASE": 0.10},
        "READ.DATA_OUT.START":       {"SIN_READ": 0.50, "MUL_READ": 0.20, "SIN_PROGRAM": 0.20, "SIN_ERASE": 0.10},
        "PROGRAM.ISSUE":             {"SIN_READ": 0.50, "MUL_READ": 0.20, "SIN_PROGRAM": 0.15, "SIN_ERASE": 0.15},
        "PROGRAM.CORE_BUSY.END":     {"SIN_READ": 0.55, "MUL_READ": 0.25, "SIN_PROGRAM": 0.10, "SIN_ERASE": 0.10},
        "ERASE.ISSUE":               {"SIN_PROGRAM": 0.40, "MUL_PROGRAM": 0.20, "SIN_READ": 0.30, "MUL_READ": 0.10},
        "ERASE.CORE_BUSY.END":       {"SIN_READ": 0.60, "MUL_READ": 0.20, "SIN_PROGRAM": 0.15, "MUL_PROGRAM": 0.05},
        "DEFAULT":                   {"SIN_READ": 0.55, "SIN_PROGRAM": 0.30, "SIN_ERASE": 0.15},
    },
    # backoff weights (phase-conditional 실패 시 사용)
    "weights": {
        "base": {"host": {"READ": 0.85, "PROGRAM": 0.10, "ERASE": 0.05, "SR": 0.00, "RESET": 0.00, "DOUT": 0.00}},
        "g_state": {"pgmable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.8},
                    "readable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.9}},
        "g_local": {"plane_busy_frac": {"low": 1.2, "mid": 1.0, "high": 0.9}},
        "g_phase": {
            "READ": {"START_NEAR": 1.2, "MID_NEAR": 0.9, "END_NEAR": 1.2},
            "PROGRAM": {"START_NEAR": 1.1, "MID_NEAR": 1.0, "END_NEAR": 0.9},
            "ERASE": {"START_NEAR": 0.9, "MID_NEAR": 1.1, "END_NEAR": 1.1},
        },
    },
    "selection": {
        "defaults": {
            "READ":    {"fanout": 1, "interleave": True},
            "PROGRAM": {"fanout": 2, "interleave": True},
            "ERASE":   {"fanout": 2, "interleave": False},
        },
        "phase_overrides": {
            "READ.CORE_BUSY.START": {"fanout": 4, "interleave": True},
        }
    },
    "op_specs": {
        "READ": {
            "scope": "PLANE_SET",
            "page_equal_required": True,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed",  "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "normal", "mean": 8.0, "std": 1.5, "min": 2.0}},
                {"name": "DATA_OUT",  "bus": True,  "dist": {"kind": "normal", "mean": 2.0, "std": 0.4, "min": 0.5}},
            ],
        },
        "PROGRAM": {
            "scope": "DIE_WIDE",
            "page_equal_required": True,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed",  "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "normal", "mean": 20.0, "std": 3.0, "min": 8.0}},
            ],
        },
        "ERASE": {
            "scope": "DIE_WIDE",
            "page_equal_required": False,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed",  "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "normal", "mean": 40.0, "std": 5.0, "min": 15.0}},
            ],
        },
        "DOUT": {
            "scope": "NONE",
            "page_equal_required": True,
            "states": [
                {"name": "ISSUE",     "bus": False, "dist": {"kind": "fixed",  "value": 0.2}},
                {"name": "DATA_OUT",  "bus": True,  "dist": {"kind": "normal", "mean": 1.0, "std": 0.2, "min": 0.2}},
            ],
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
    "topology": {"dies": 1, "planes_per_die": 4, "blocks_per_plane": 8, "pages_per_block": 16},
    "export": {"tu_us": 0.01, "nop_symbol": "NOP", "wait_as_nop": True, "drift_correction": True},
}

# ---------------------------- Alias mapping ----------------------------
OP_ALIAS = {
    "SIN_READ":    {"base": "READ",    "fanout": ("eq", 1)},
    "MUL_READ":    {"base": "READ",    "fanout": ("ge", 2)},
    "SIN_PROGRAM": {"base": "PROGRAM", "fanout": ("eq", 1)},
    "MUL_PROGRAM": {"base": "PROGRAM", "fanout": ("ge", 2)},
    "SIN_ERASE":   {"base": "ERASE",   "fanout": ("eq", 1)},
    "MUL_ERASE":   {"base": "ERASE",   "fanout": ("ge", 2)},
}

# ---------------------------- Models ----------------------------
class OpKind(Enum):
    READ=auto(); DOUT=auto(); PROGRAM=auto(); ERASE=auto(); SR=auto(); RESET=auto()

class Scope(Enum):
    NONE=0; PLANE_SET=1; DIE_WIDE=2

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
    bus: bool = False

@dataclass
class Operation:
    kind: OpKind
    targets: List[Address]
    states: List[StateSeg]
    movable: bool = True
    meta: Dict[str,Any] = field(default_factory=dict)  # scope, plane_list, arity, alias_used, etc.

# ---------------------------- Utility ----------------------------
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

def parse_hook_key(label: str):
    parts = label.split(".")
    if len(parts) >= 3: return parts[0], parts[1], parts[2]
    if len(parts) == 2: return parts[0], parts[1], None
    return None, None, None

def get_phase_dist(cfg: Dict[str,Any], hook_label: str):
    op, state, pos = parse_hook_key(hook_label)
    pc = cfg.get("phase_conditional", {})
    candidates = []
    if op and state and pos: candidates.append(f"{op}.{state}.{pos}")
    if op and state:         candidates.append(f"{op}.{state}")
    candidates.append("DEFAULT")
    for key in candidates:
        dist = pc.get(key)
        if dist and sum(dist.values()) > 0:
            return dist, key
    return None, None

def roulette_pick(dist: Dict[str, float], allow: set) -> Optional[str]:
    items = [(name, p) for name, p in dist.items() if name in allow and p > 0.0]
    if not items: return None
    total = sum(p for _, p in items)
    r = random.random()*total
    acc=0.0
    pick = items[-1][0]
    for name, p in items:
        acc += p
        if r <= acc:
            pick = name; break
    return pick

def resolve_alias(name: str) -> Tuple[str, Optional[Tuple[str,int]]]:
    """returns (base_name, fanout_constraint or None)"""
    if name in OP_ALIAS:
        base = OP_ALIAS[name]["base"]
        return base, OP_ALIAS[name]["fanout"]
    return name, None

# ---------------------------- OpSpec & builders ----------------------------
def build_operation(kind: OpKind, cfg_op: Dict[str, Any], targets: List[Address]) -> Operation:
    states=[]
    for s in cfg_op["states"]:
        dur = sample_dist(s["dist"])
        states.append(StateSeg(name=s["name"], dur_us=dur, bus=bool(s.get("bus", False))))
    return Operation(kind=kind, targets=targets, states=states)

def get_op_duration(op: Operation) -> float:
    return sum(seg.dur_us for seg in op.states)

# ---------------------------- Address / Bus Managers ----------------------------
class AddressManager:
    def __init__(self, cfg: Dict[str, Any]):
        topo = cfg["topology"]
        self.cfg = cfg
        self.dies = topo["dies"]
        self.planes = topo["planes_per_die"]
        self.pages_per_block = topo["pages_per_block"]
        self.blocks_per_plane = topo["blocks_per_plane"]

        self.available: Dict[Tuple[int,int], float] = {(0,p): 0.0 for p in range(self.planes)}  # plane availability
        self.cursors: Dict[Tuple[int,int], List[int]] = {(0,p): [0,0,0] for p in range(self.planes)}  # [block, next_pgm_page, next_rd_page]
        self.programmed: Dict[Tuple[int,int], Set[Tuple[int,int]]] = {(0,p): set() for p in range(self.planes)}  # (block,page) programmed pages
        self.resv: Dict[Tuple[int,int], List[Tuple[float,float,Optional[int]]]] = {(0,p): [] for p in range(self.planes)}  # per-plane (start,end,block)

        # Global bus reservation (ISSUE/DATA_OUT etc)
        self.bus_resv: List[Tuple[float,float]] = []  # [(start,end)] sorted by insertion time

    # ---- observation ----
    def available_at(self, die:int, plane:int) -> float:
        return self.available[(die,plane)]

    def earliest_start_for_scope(self, die:int, scope: Scope, plane_set: Optional[List[int]]=None) -> float:
        if scope == Scope.DIE_WIDE:
            planes = list(range(self.planes))
        elif scope == Scope.PLANE_SET and plane_set is not None:
            planes = plane_set
        else:
            planes = [plane_set[0]] if plane_set else [0]
        return max(self.available[(die,p)] for p in planes)

    def observe_states(self, die:int, plane:int, now_us: float):
        prog = len(self.programmed[(die,plane)])
        pgmable_ratio  = "mid" if prog < 10 else "low"
        readable_ratio = "mid" if prog > 0 else "low"
        plane_busy_frac = "high" if self.available_at(die,plane) > now_us else "low"
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls": "host"},
                {"plane_busy_frac": plane_busy_frac})

    # ---- bus segments helpers ----
    def bus_segments_for_op(self, op: Operation) -> List[Tuple[float,float]]:
        """relative offsets from op start for bus=true states"""
        segs=[]
        t=0.0
        for s in op.states:
            s0, s1 = t, t + s.dur_us
            if s.bus: segs.append((s0, s1))
            t = s1
        return segs

    def bus_precheck(self, start_hint: float, segs: List[Tuple[float,float]]) -> bool:
        """Check global bus availability for absolute segments"""
        for (off0, off1) in segs:
            a0, a1 = quantize(start_hint + off0), quantize(start_hint + off1)
            for (s,e) in self.bus_resv:
                if not (a1 <= s or e <= a0):
                    return False
        return True

    def bus_reserve(self, start_time: float, segs: List[Tuple[float,float]]):
        for (off0, off1) in segs:
            a0, a1 = quantize(start_time + off0), quantize(start_time + off1)
            self.bus_resv.append((a0,a1))

    # ---- planner: non-greedy multi-plane selection with degrade ----
    def _random_plane_sets(self, fanout:int, tries:int, start_plane:int) -> List[List[int]]:
        P = list(range(self.planes))
        out=[]
        for _ in range(tries):
            # bias: include start_plane with higher chance
            cand = set(random.sample(P, min(fanout, len(P))))
            if start_plane not in cand and random.random()<0.6:
                cand.pop() if len(cand)>0 else None
                cand.add(start_plane)
            out.append(sorted(cand))
        # unique
        uniq=[]
        seen=set()
        for ps in out:
            key=tuple(ps)
            if key not in seen and len(ps)==fanout:
                uniq.append(ps); seen.add(key)
        return uniq

    def _pages_present(self, die:int, plane:int) -> Set[int]:
        return {p for (_,p) in self.programmed[(die,plane)]}

    def plan_multipane(self, kind: OpKind, die:int, start_plane:int, desired_fanout:int, interleave:bool) -> Optional[Tuple[List[Address], List[int], Scope]]:
        if desired_fanout < 1: desired_fanout = 1
        tries = self.cfg["policy"]["planner_max_tries"]
        # degrade loop
        for f in range(desired_fanout, 0, -1):
            # candidate plane-sets
            candidates = self._random_plane_sets(f, tries, start_plane)
            for plane_set in candidates:
                # alignment per kind
                if kind == OpKind.READ:
                    # page must be common across planes (block may differ)
                    commons = None
                    for pl in plane_set:
                        pages = self._pages_present(die, pl)
                        commons = pages if commons is None else (commons & pages)
                        if not commons:
                            break
                    if not commons:
                        continue
                    page = random.choice(sorted(list(commons)))
                    targets=[]
                    for pl in plane_set:
                        # choose any block that has page
                        blks = [b for (b,p) in self.programmed[(die,pl)] if p==page]
                        blk = blks[0] if blks else 0
                        targets.append(Address(die, pl, block=blk, page=page))
                    scope = Scope.PLANE_SET

                elif kind == OpKind.PROGRAM:
                    # next_pgm_page must match across planes
                    ok=True
                    nxt=[]
                    for pl in plane_set:
                        b, pgm_p, _ = self.cursors[(die,pl)]
                        nxt.append(pgm_p)
                    ok = len(set(nxt))==1
                    if not ok:
                        continue
                    page = nxt[0]
                    targets=[]
                    for pl in plane_set:
                        b, pgm_p, _ = self.cursors[(die,pl)]
                        targets.append(Address(die, pl, block=b, page=page))
                    scope = Scope.DIE_WIDE  # die-wide CORE_BUSY

                elif kind == OpKind.ERASE:
                    targets=[]
                    for pl in plane_set:
                        b = self.cursors[(die,pl)][0]
                        targets.append(Address(die, pl, block=b, page=None))
                    scope = Scope.DIE_WIDE  # die-wide CORE_BUSY

                else:
                    continue

                return targets, plane_set, scope
        return None

    # ---- precheck / reserve / commit ----
    def precheck(self, kind: OpKind, targets: List[Address], start_hint: float, scope: Scope) -> bool:
        """Time overlap + simple block/page conflict (scope-aware)."""
        start_hint = quantize(start_hint); end_hint = start_hint  # guard = 0
        die = targets[0].die
        # choose planes to check (scope)
        if scope == Scope.DIE_WIDE:
            planes = {(die,p) for p in range(self.planes)}
        else:
            planes = {(t.die, t.plane) for t in targets}
        # time overlap
        for (d,p) in planes:
            for (s,e,_) in self.resv[(d,p)]:
                if not (end_hint <= s or e <= start_hint):
                    return False
        # simple block/page conflict rules (placeholder; extend in next step)
        # e.g., forbid ERASE with READ/PROGRAM on same block overlapping window
        return True

    def reserve(self, op: Operation, start: float, end: float):
        """scope-aware plane reservation; also update available times."""
        die = op.targets[0].die
        start = quantize(start); end = quantize(end)
        if op.meta.get("scope") == "DIE_WIDE":
            planes = [(die,p) for p in range(self.planes)]
        else:
            planes = [(t.die, t.plane) for t in op.targets]
        for (d,p) in planes:
            self.available[(d,p)] = max(self.available[(d,p)], end)
            self.resv[(d,p)].append((start, end, None))

    def commit(self, op: Operation):
        # update address state for PROGRAM/ERASE
        # (READ does not change programmed set)
        for t in op.targets:
            key = (t.die, t.plane)
            if op.kind == OpKind.PROGRAM and t.page is not None:
                self.programmed[key].add((t.block, t.page))
                # advance cursor
                b, pgm_p, rd = self.cursors[(t.die, t.plane)]
                pgm_p = (pgm_p + 1)
                if pgm_p >= self.pages_per_block:
                    pgm_p = 0
                    b = (b+1) % self.blocks_per_plane
                self.cursors[(t.die, t.plane)] = [b, pgm_p, rd]
            elif op.kind == OpKind.ERASE:
                # clear all pages in block
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
        # READ 종료 → DOUT 의무 (op.targets 전체를 그대로 사용 = plane-set 단일 오퍼레이션)
        for spec in self.specs:
            if spec["issuer"] == op.kind.name:
                dt = sample_dist(spec["window_us"])
                ob = Obligation(
                    require = OpKind[spec["require"]],
                    targets = op.targets,  # 그대로 전달 → DOUT도 plane-set 하나의 op
                    deadline_us = quantize(now_us + dt),
                    boost_factor = spec["priority_boost"]["boost_factor"],
                    hard_slot = spec["priority_boost"].get("hard_slot", False),
                )
                heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._seq, ob=ob))
                self._seq += 1
                first = op.targets[0]
                print(f"[{now_us:7.2f} us] OBLIG  created: {op.kind.name} -> {ob.require.name} by {ob.deadline_us:7.2f} us, target(d0,p{first.plane}) page={first.page}")

    def pop_urgent(self, now_us: float, die:int, plane:int,
                   horizon_us: float, earliest_start: float) -> Optional[Obligation]:
        if not self.heap: return None
        kept: List[_ObHeapItem] = []
        chosen: Optional[Obligation] = None
        now_us = quantize(now_us)
        earliest_start = quantize(earliest_start)
        while self.heap and not chosen:
            item = heapq.heappop(self.heap)
            ob = item.ob
            # obligation plane-set includes this plane?
            plane_list = {a.plane for a in ob.targets}
            same_die = (ob.targets[0].die == die)
            same_plane = plane in plane_list
            in_horizon = ((ob.deadline_us - now_us) <= max(horizon_us, 0.0)) or ob.hard_slot
            feasible_time = (earliest_start <= ob.deadline_us)
            if same_die and same_plane and in_horizon and feasible_time:
                chosen = ob; break
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
        self.stats = {"alias_degrade": 0}

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

    def _fanout_from_alias(self, base_name: str, alias_constraint: Optional[Tuple[str,int]], hook_label: str) -> Tuple[int,bool]:
        # phase override or defaults
        fanout, interleave = get_phase_selection_override(self.cfg, hook_label, base_name)
        if alias_constraint:
            mode, val = alias_constraint
            if mode == "eq":
                fanout = val
            elif mode == "ge":
                fanout = max(fanout, val)
        fanout = max(1, fanout)
        return fanout, interleave

    def propose(self, now_us: float, hook: PhaseHook,
                global_state: Dict[str,str], local_state: Dict[str,str],
                earliest_start: float) -> Optional[Operation]:
        die, hook_plane = hook.die, hook.plane

        # 0) Obligation first
        ob = self.obl.pop_urgent(now_us, die, hook_plane, horizon_us=10.0, earliest_start=earliest_start)
        if ob:
            # Build op for bus check
            cfg_op = self.cfg["op_specs"][ob.require.name]
            op = build_operation(ob.require, cfg_op, ob.targets)
            # scope/meta
            op.meta["scope"] = self.cfg["op_specs"][op.kind.name]["scope"]
            op.meta["plane_list"] = sorted({a.plane for a in op.targets})
            op.meta["arity"] = len(op.meta["plane_list"])
            # earliest start for scope set
            scope = Scope[op.meta["scope"]]
            plane_set = op.meta["plane_list"]
            start_hint = self.addr.earliest_start_for_scope(die, scope, plane_set)
            if self.addr.precheck(op.kind, op.targets, start_hint, scope) and \
               self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op)):
                op.meta["source"]="obligation"; op.meta["phase_key_used"]="(obligation)"; return op
            # else: cannot service obligation now → let scheduler try later (no force)

        # 1) Phase-conditional distribution (allow alias keys)
        allow = set(list(OP_ALIAS.keys()) + ["READ","PROGRAM","ERASE"])
        dist, used_key = get_phase_dist(self.cfg, hook.label)
        if dist:
            pick = roulette_pick(dist, allow)
            if pick:
                base_name, alias_const = resolve_alias(pick)
                try_kind = OpKind[base_name]
                fanout, interleave = self._fanout_from_alias(base_name, alias_const, hook.label)
                # multi-plane planning with degrade
                planned = self.addr.plan_multipane(try_kind, die, hook_plane, fanout, interleave)
                if not planned and fanout>1:
                    # degrade to single-plane
                    self.stats["alias_degrade"] += 1
                    planned = self.addr.plan_multipane(try_kind, die, hook_plane, 1, interleave)
                if planned:
                    targets, plane_set, scope = planned
                    cfg_op = self.cfg["op_specs"][base_name]
                    op = build_operation(try_kind, cfg_op, targets)
                    op.meta["scope"] = cfg_op["scope"]
                    op.meta["plane_list"] = plane_set
                    op.meta["arity"] = len(plane_set)
                    op.meta["alias_used"] = pick
                    op.meta["fanout_req"] = fanout
                    op.meta["interleave"] = interleave
                    # scope-aware start hint (across plane_set or die-wide)
                    start_hint = self.addr.earliest_start_for_scope(die, scope, plane_set)
                    if self.addr.precheck(try_kind, targets, start_hint, scope) and \
                       self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op)):
                        op.meta["source"]="policy.phase_conditional"; op.meta["phase_key_used"]=used_key
                        return op
                # dist 있었지만 실패 → score backoff로

        # 2) Backoff: score policy (single-plane default)
        cand=[]
        for name in ["READ","PROGRAM","ERASE"]:
            s=self._score(name, hook.label, global_state, local_state)
            if s>0: cand.append((name,s))
        if not cand: return None
        total=sum(s for _,s in cand)
        r=random.random()*total; acc=0.0; pick=cand[-1][0]
        for name,s in cand:
            acc+=s
            if r<=acc: pick=name; break
        try_kind=OpKind[pick]
        # single-plane plan
        planned = self.addr.plan_multipane(try_kind, die, hook_plane, 1, True)
        if not planned: return None
        targets, plane_set, scope = planned
        cfg_op = self.cfg["op_specs"][pick]
        op = build_operation(try_kind, cfg_op, targets)
        op.meta["scope"]=cfg_op["scope"]; op.meta["plane_list"]=plane_set; op.meta["arity"]=len(plane_set)
        start_hint = self.addr.earliest_start_for_scope(die, scope, plane_set)
        if not (self.addr.precheck(try_kind, targets, start_hint, scope) and \
                self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op))):
            return None
        op.meta["source"]="policy.score_backoff"; op.meta["phase_key_used"]="(score_backoff)"
        return op

# ---------------------------- Scheduler (with Bus gating) ----------------------------
def _addr_str(a: Address)->str:
    return f"(d{a.die},p{a.plane},b{a.block},pg{a.page})"

class Scheduler:
    def __init__(self, cfg, addr: AddressManager, spe: PolicyEngine, obl: ObligationManager):
        self.cfg=cfg; self.addr=addr; self.SPE=spe; self.obl=obl
        self.now=0.0
        self.ev=[]  # (time, seq, type, payload)
        self._seq=0
        self.stat_propose_calls=0; self.stat_scheduled=0
        self._push(0.0, "QUEUE_REFILL", None)
        for plane in range(self.addr.planes):
            self._push(0.0, "PHASE_HOOK", PhaseHook(0.0, "BOOT.START", 0, plane))

    def _push(self, t: float, typ: str, payload: Any):
        heapq.heappush(self.ev, (quantize(t), self._seq, typ, payload)); self._seq+=1

    def _start_time_for_op(self, op: Operation) -> float:
        die = op.targets[0].die
        scope = Scope[op.meta.get("scope","PLANE_SET")]
        plane_set = [a.plane for a in op.targets]
        # plane availability
        t_planes = self.addr.earliest_start_for_scope(die, scope, plane_set)
        # and bus availability (shift to avoid conflicts if needed)
        segs = self.addr.bus_segments_for_op(op)
        t = max(self.now, t_planes)
        # (간단) 충돌 시에는 propose 단계에서 이미 걸렀다고 가정 → t 그대로
        return quantize(t)

    def _schedule_operation(self, op: Operation):
        start = self._start_time_for_op(op)
        dur = get_op_duration(op)
        end = quantize(start + dur)
        # reserve: scope-aware
        self.addr.reserve(op, start, end)
        # reserve bus
        self.addr.bus_reserve(start, self.addr.bus_segments_for_op(op))
        # events & hooks
        self._push(start, "OP_START", op)
        self._push(end,   "OP_END",   op)
        # phase hooks: per plane
        for t in op.targets:
            # Label은 op.kind 기반; 훅은 bus 여부와 무관하게 생성(정책 트리거 용)
            hooks=[]
            cur = start
            for s in op.states:
                # START
                hooks.append(PhaseHook(cur, f"{op.kind.name}.{s.name}.START", t.die, t.plane))
                # MID (only for "CORE_BUSY" as example)
                if s.name=="CORE_BUSY":
                    hooks.append(PhaseHook(cur + s.dur_us*0.5, f"{op.kind.name}.{s.name}.MID", t.die, t.plane))
                # END
                hooks.append(PhaseHook(cur + s.dur_us, f"{op.kind.name}.{s.name}.END", t.die, t.plane))
                cur += s.dur_us
            for h in hooks:
                self._push(h.time_us, "PHASE_HOOK", h)

        first = op.targets[0]
        print(f"[{self.now:7.2f} us] SCHED  {op.kind.name:7s} arity={op.meta.get('arity')} scope={op.meta.get('scope')} start={start:7.2f} end={end:7.2f} 1st={_addr_str(first)} src={op.meta.get('source')} alias={op.meta.get('alias_used')}")

        self.stat_scheduled += 1

    def run_until(self, t_end: float):
        t_end = quantize(t_end)
        while self.ev and self.ev[0][0] <= t_end:
            self.now, _, typ, payload = heapq.heappop(self.ev)

            if typ=="QUEUE_REFILL":
                for plane in range(self.addr.planes):
                    self._push(self.now, "PHASE_HOOK", PhaseHook(self.now, "REFILL.NUDGE", 0, plane))
                self._push(self.now + self.cfg["policy"]["queue_refill_period_us"], "QUEUE_REFILL", None)

            elif typ=="PHASE_HOOK":
                hook: PhaseHook = payload
                earliest_start = self.addr.available_at(hook.die, hook.plane)
                global_state, local_state = self.addr.observe_states(hook.die, hook.plane, self.now)
                self.stat_propose_calls += 1
                op = self.SPE.propose(self.now, hook, global_state, local_state, earliest_start)
                if op:
                    self._schedule_operation(op)

            elif typ=="OP_START":
                op: Operation = payload
                first = op.targets[0]
                print(f"[{self.now:7.2f} us] START  {op.kind.name:7s} arity={op.meta.get('arity')} target={_addr_str(first)}")

            elif typ=="OP_END":
                op: Operation = payload
                first = op.targets[0]
                print(f"[{self.now:7.2f} us] END    {op.kind.name:7s} arity={op.meta.get('arity')} target={_addr_str(first)}")
                self.addr.commit(op)
                self.obl.on_commit(op, self.now)

        print(f"\n=== Stats ===")
        print(f"propose calls : {self.stat_propose_calls}")
        print(f"scheduled ops : {self.stat_scheduled}")
        if self.stat_propose_calls:
            rate = 100.0 * self.stat_scheduled / self.stat_propose_calls
            print(f"accept ratio  : {rate:.1f}%")
        print(f"alias degrade : {self.SPE.stats.get('alias_degrade',0)}")

# ---------------------------- Selection helpers (phase overrides) ----------------------------
def get_phase_selection_override(cfg: Dict[str,Any], hook_label: str, kind_name: str):
    op, state, pos = parse_hook_key(hook_label)
    po = cfg.get("selection", {}).get("phase_overrides", {})
    keys = []
    if op and state and pos: keys.append(f"{op}.{state}.{pos}")
    if op and state:         keys.append(f"{op}.{state}")
    for k in keys:
        val = po.get(k)
        if val:
            f = int(val.get("fanout", 1))
            iv = bool(val.get("interleave", True))
            return f, iv
    dflt = cfg.get("selection", {}).get("defaults", {}).get(kind_name, {"fanout":1,"interleave":True})
    return int(dflt.get("fanout",1)), bool(dflt.get("interleave", True))

# ---------------------------- Main ----------------------------
def main():
    random.seed(CFG["rng_seed"])
    addr = AddressManager(CFG)
    obl  = ObligationManager(CFG["obligations"])
    spe  = PolicyEngine(CFG, addr, obl)
    sch  = Scheduler(CFG, addr, spe, obl)
    print("=== NAND Sequence Generator (Alias + Multi-plane + DIE_WIDE + Bus) ===")
    sch.run_until(CFG["policy"]["run_until_us"])
    print("=== Done ===")

if __name__ == "__main__":
    main()