# nandsim_p5_allinone.py
# - P5: Stats + Admission gating + Block/Plane redefinition
# - AddressManager v2: committed/future rails (no-dup PROGRAM; READ uses committed visibility)
# - Block/Plane: plane = block % planes; topology: {dies, planes, blocks, pages_per_block}
# - Policy admission gating (near-future only; phase/op tunable; obligations bypass)
# - Obligation stats: created/assigned/fulfilled/fulfilled_in_time/expired
# - Multi-plane alias (SIN_*/MUL_*), DIE_WIDE CORE_BUSY, Bus gating, SR, constraints incl. DOUT freeze

from __future__ import annotations
import heapq, random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple, Set
from viz_tools import TimelineLogger, plot_gantt, plot_gantt_by_die
from viz_tools import plot_block_page_sequence_3d, plot_block_page_sequence_3d_by_die
from viz_tools import validate_timeline, print_validation_report, violations_to_dataframe

# --------------------------------------------------------------------------
# Simulation resolution
SIM_RES_US = 0.01
def quantize(t: float) -> float:
    return round(t / SIM_RES_US) * SIM_RES_US

# --------------------------------------------------------------------------
# Config
CFG = {
    "rng_seed": 12345,
    "policy": {
        "queue_refill_period_us": 3.0,
        "run_until_us": 180.0,
        "planner_max_tries": 8,
    },

    # Admission gating (near-future only to prevent policy explosion)
    "admission": {
        "default_delta_us": 0.30,
        "phase_overrides": {
            "CORE_BUSY": 0.05,
            "ISSUE":     0.10,
            "DATA_OUT":  0.05,
            "BOOT":      0.30,
            "NUDGE":     0.10,  # from REFILL.NUDGE hooks
        },
        "op_overrides": {
            "SR": 0.30
        },
        "obligation_bypass": True  # obligations ignore admission delta by default
    },

    # Phase-conditional proposal (alias keys allowed)
    "phase_conditional": {
        "READ.ISSUE":                {"MUL_PROGRAM": 0.15, "SIN_PROGRAM": 0.10, "SIN_READ": 0.35, "MUL_READ": 0.25, "SIN_ERASE": 0.15, "SR": 0.00},
        "READ.CORE_BUSY.START":      {"MUL_READ": 0.50, "SIN_READ": 0.20, "SIN_PROGRAM": 0.10, "SIN_ERASE": 0.10, "SR": 0.10},
        "READ.DATA_OUT.START":       {"SIN_READ": 0.50, "MUL_READ": 0.20, "SIN_PROGRAM": 0.15, "SIN_ERASE": 0.10, "SR": 0.05},

        "PROGRAM.ISSUE":             {"SIN_READ": 0.50, "MUL_READ": 0.20, "SIN_PROGRAM": 0.15, "SIN_ERASE": 0.10, "SR": 0.05},
        "PROGRAM.CORE_BUSY":         {"SR": 1.0},   # during PROGRAM core-busy: only SR
        "PROGRAM.CORE_BUSY.END":     {"SIN_READ": 0.55, "MUL_READ": 0.25, "SIN_PROGRAM": 0.10, "SIN_ERASE": 0.05, "SR": 0.05},

        "ERASE.ISSUE":               {"SIN_PROGRAM": 0.40, "MUL_PROGRAM": 0.20, "SIN_READ": 0.25, "MUL_READ": 0.10, "SR": 0.05},
        "ERASE.CORE_BUSY":           {"SR": 1.0},   # during ERASE core-busy: only SR
        "ERASE.CORE_BUSY.END":       {"SIN_READ": 0.50, "MUL_READ": 0.20, "SIN_PROGRAM": 0.10, "MUL_PROGRAM": 0.05, "SR": 0.15},

        "MUL_READ.CORE_BUSY":        {"SR": 1.0},   # during MUL_READ core-busy: only SR
        "SIN_READ.CORE_BUSY":        {"SIN_READ": 0.6, "SR": 0.4},  # during SIN_READ core-busy: allow SIN_READ or SR; forbid MUL/PGM/ERASE

        "DEFAULT":                   {"SIN_READ": 0.55, "SIN_PROGRAM": 0.25, "SIN_ERASE": 0.10, "SR": 0.10},
    },

    # Backoff scoring weights (used if phase_conditional fails)
    "weights": {
        "base": {"host": {"READ": 0.70, "PROGRAM": 0.15, "ERASE": 0.05, "SR": 0.10, "RESET": 0.00, "DOUT": 0.00}},
        "g_state": {"pgmable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.8},
                    "readable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.9}},
        "g_local": {"plane_busy_frac": {"low": 1.2, "mid": 1.0, "high": 0.9}},
        "g_phase": {
            "READ": {"START_NEAR": 1.2, "MID_NEAR": 0.9, "END_NEAR": 1.2},
            "PROGRAM": {"START_NEAR": 1.1, "MID_NEAR": 1.0, "END_NEAR": 0.9},
            "ERASE": {"START_NEAR": 0.9, "MID_NEAR": 1.1, "END_NEAR": 1.1},
            "SR": {"START_NEAR": 1.1, "MID_NEAR": 1.1, "END_NEAR": 1.1},
        },
    },

    # Selection defaults/overrides (for fanout/interleave)
    "selection": {
        "defaults": {
            "READ":    {"fanout": 1, "interleave": True},
            "PROGRAM": {"fanout": 2, "interleave": True},
            "ERASE":   {"fanout": 2, "interleave": False},
            "SR":      {"fanout": 1, "interleave": True},
        },
        "phase_overrides": {
            "READ.CORE_BUSY.START": {"fanout": 4, "interleave": True},
        }
    },

    # Op specs (state sequence + bus usage; scope for CORE_BUSY occupancy)
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
        "SR": {
            "scope": "NONE",
            "page_equal_required": False,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed",  "value": 0.2}},
                {"name": "DATA_OUT",  "bus": True,  "dist": {"kind": "fixed",  "value": 0.3}},
            ],
        },
    },

    # Runtime exclusion rules (register on schedule; checked on propose)
    "constraints": {
        "exclusions": [
            # PROGRAM/ERASE: during CORE_BUSY block READ/PROGRAM/ERASE on the die; allow SR
            {"when": {"op": "PROGRAM", "states": ["CORE_BUSY"]}, "scope": "DIE",
             "blocks": ["BASE:READ", "BASE:PROGRAM", "BASE:ERASE"]},
            {"when": {"op": "ERASE",   "states": ["CORE_BUSY"]}, "scope": "DIE",
             "blocks": ["BASE:READ", "BASE:PROGRAM", "BASE:ERASE"]},

            # MUL_READ: during CORE_BUSY block READ/PROGRAM/ERASE (on the die); allow SR
            {"when": {"op": "READ", "alias": "MUL", "states": ["CORE_BUSY"]}, "scope": "DIE",
             "blocks": ["BASE:READ", "BASE:PROGRAM", "BASE:ERASE"]},

            # SIN_READ: during CORE_BUSY block MUL_READ + PROGRAM + ERASE; allow SIN_READ & SR
            {"when": {"op": "READ", "alias": "SIN", "states": ["CORE_BUSY"]}, "scope": "DIE",
             "blocks": ["ALIAS:MUL_READ", "BASE:PROGRAM", "BASE:ERASE"]},

            # DOUT: freeze all ops globally across its full duration
            {"when": {"op": "DOUT", "states": ["*"]}, "scope": "GLOBAL", "blocks": ["ANY"]},
        ]
    },

    # Obligation: READ → DOUT
    "obligations": [
        {
            "issuer": "READ",
            "require": "DOUT",
            "window_us": {"kind": "normal", "mean": 6.0, "std": 1.5, "min": 1.0},
            "priority_boost": {"start_us_before_deadline": 2.5, "boost_factor": 2.0, "hard_slot": True},
        }
    ],

    # Topology (redefined): plane = block % planes
    "topology": {
        "dies": 1,
        "planes": 4,
        "blocks": 32,           # total blocks per die (not per plane)
        "pages_per_block": 16,
    },

    "export": {"tu_us": 0.01, "nop_symbol": "NOP", "wait_as_nop": True, "drift_correction": True},

    # Address initial state: -1=ERASED, -2=initial(not erased)
    "address_init_state": -1,
}

# --------------------------------------------------------------------------
# Alias mapping for policy/external naming
OP_ALIAS = {
    "SIN_READ":    {"base": "READ",    "fanout": ("eq", 1)},
    "MUL_READ":    {"base": "READ",    "fanout": ("ge", 2)},
    "SIN_PROGRAM": {"base": "PROGRAM", "fanout": ("eq", 1)},
    "MUL_PROGRAM": {"base": "PROGRAM", "fanout": ("ge", 2)},
    "SIN_ERASE":   {"base": "ERASE",   "fanout": ("eq", 1)},
    "MUL_ERASE":   {"base": "ERASE",   "fanout": ("ge", 2)},
}

# --------------------------------------------------------------------------
# Models
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
    meta: Dict[str,Any] = field(default_factory=dict)

# --------------------------------------------------------------------------
# Utils
def sample_dist(d: Dict[str, Any]) -> float:
    k = d["kind"]
    if k == "fixed": return float(d["value"])
    if k == "normal":
        m, s, mn = d["mean"], d["std"], d.get("min", 0.0)
        v = random.gauss(m, s); return max(v, mn)
    if k == "exp": return random.expovariate(d["lambda"])
    raise ValueError(f"unknown dist kind: {k}")

def parse_hook_key(label: str):
    parts = label.split(".")
    if len(parts) >= 3: return parts[0], parts[1], parts[2]
    if len(parts) == 2: return parts[0], parts[1], None
    return None, None, None

def get_phase_dist(cfg: Dict[str,Any], hook_label: str):
    op, state, pos = parse_hook_key(hook_label)
    pc = cfg.get("phase_conditional", {})
    keys=[]
    if op and state and pos: keys.append(f"{op}.{state}.{pos}")
    if op and state:         keys.append(f"{op}.{state}")
    keys.append("DEFAULT")
    for key in keys:
        dist = pc.get(key)
        if dist and sum(dist.values())>0: return dist, key
    return None, None

def roulette_pick(dist: Dict[str, float], allow: set) -> Optional[str]:
    items=[(n,p) for n,p in dist.items() if n in allow and p>0]
    if not items: return None
    tot=sum(p for _,p in items); r=random.random()*tot; acc=0.0; pick=items[-1][0]
    for n,p in items:
        acc+=p
        if r<=acc: pick=n; break
    return pick

def resolve_alias(name: str) -> Tuple[str, Optional[Tuple[str,int]]]:
    if name in OP_ALIAS: return OP_ALIAS[name]["base"], OP_ALIAS[name]["fanout"]
    return name, None

def get_admission_delta(cfg: Dict[str,Any], hook_label: str, op_kind_name: str) -> float:
    _, state, _ = parse_hook_key(hook_label)
    adm = cfg.get("admission", {})
    if state and state in adm.get("phase_overrides", {}):
        return float(adm["phase_overrides"][state])
    if op_kind_name in adm.get("op_overrides", {}):
        return float(adm["op_overrides"][op_kind_name])
    return float(adm.get("default_delta_us", 0.3))

# --------------------------------------------------------------------------
# Builders
def build_operation(kind: OpKind, cfg_op: Dict[str, Any], targets: List[Address]) -> Operation:
    states=[]
    for s in cfg_op["states"]:
        states.append(StateSeg(name=s["name"], dur_us=sample_dist(s["dist"]), bus=bool(s.get("bus", False))))
    return Operation(kind=kind, targets=targets, states=states)

def get_op_duration(op: Operation) -> float:
    return sum(seg.dur_us for seg in op.states)

def get_timeline_df(self):
    return self.logger.to_dataframe()

# --------------------------------------------------------------------------
# Address & Bus Managers (with block/plane redefinition + state rails)
class AddressManager:
    """
    v2 상태 모델 (block-scoped):
    - addr_state_committed[(die, block)] : int  # last committed program page (-1=ERASED, -2=initial)
    - addr_state_future[(die, block)]    : int  # future after reservations
    - programmed_committed[(die)] : Set[(block, page)]
    - write_head[(die, plane)] : int  # block index assigned to plane (block % planes == plane)
    """
    def __init__(self, cfg: Dict[str, Any]):
        topo=cfg["topology"]; self.cfg=cfg
        self.dies=topo["dies"]; self.planes=topo["planes"]
        self.blocks=topo["blocks"]; self.pages_per_block=topo["pages_per_block"]

        self.available={(0,p):0.0 for p in range(self.planes)}  # per-plane availability time
        self.resv={(0,p):[] for p in range(self.planes)}        # per-plane reservations: (start,end,block)
        self.bus_resv: List[Tuple[float,float]] = []            # global bus reservations

        init_state = int(cfg.get("address_init_state", -1))
        self.addr_state_committed: Dict[Tuple[int,int], int] = {}
        self.addr_state_future:    Dict[Tuple[int,int], int] = {}
        self.programmed_committed: Dict[Tuple[int], Set[Tuple[int,int]]] = {(0,): set()}
        self.write_head: Dict[Tuple[int,int], int] = {}  # (die,plane)->block

        for b in range(self.blocks):
            self.addr_state_committed[(0,b)] = init_state
            self.addr_state_future[(0,b)]    = init_state
        for p in range(self.planes):
            self.write_head[(0,p)] = p  # first block of that plane stripe

    # ---- helpers for plane/block mapping ----
    def plane_of(self, block:int) -> int:
        return block % self.planes

    def iter_blocks_of_plane(self, plane:int):
        for b in range(plane, self.blocks, self.planes):
            yield b

    # ---- observations ----
    def available_at(self, die:int, plane:int)->float: return self.available[(die,plane)]

    def earliest_start_for_scope(self, die:int, scope: Scope, plane_set: Optional[List[int]]=None)->float:
        if scope==Scope.DIE_WIDE:
            planes=list(range(self.planes))
        elif scope==Scope.PLANE_SET and plane_set is not None:
            planes=plane_set
        else:
            planes=[plane_set[0]] if plane_set else [0]
        return max(self.available[(die,p)] for p in planes)

    def observe_states(self, die:int, plane:int, now_us: float):
        pgmable_blocks=0; readable_blocks=0
        for b in self.iter_blocks_of_plane(plane):
            fut=self.addr_state_future[(die,b)]
            com=self.addr_state_committed[(die,b)]
            if fut < self.pages_per_block-1: pgmable_blocks += 1
            if com >= 0: readable_blocks += 1
        total=max(1, len(list(self.iter_blocks_of_plane(plane))))
        def bucket(x):
            r=x/total
            return "low" if r<0.34 else ("mid" if r<0.67 else "high")
        pgmable_ratio=bucket(pgmable_blocks)
        readable_ratio=bucket(readable_blocks)
        plane_busy_frac="high" if self.available_at(die,plane)>now_us else "low"
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls":"host"},
                {"plane_busy_frac": plane_busy_frac})

    # ---- bus segments & gating ----
    def bus_segments_for_op(self, op: Operation)->List[Tuple[float,float]]:
        segs=[]; t=0.0
        for s in op.states:
            if s.bus: segs.append((t, t+s.dur_us))
            t+=s.dur_us
        return segs

    def bus_precheck(self, start_hint: float, segs: List[Tuple[float,float]])->bool:
        for (off0,off1) in segs:
            a0, a1 = quantize(start_hint+off0), quantize(start_hint+off1)
            for (s,e) in self.bus_resv:
                if not (a1<=s or e<=a0): return False
        return True

    def bus_reserve(self, start_time: float, segs: List[Tuple[float,float]]):
        for (off0,off1) in segs:
            self.bus_resv.append((quantize(start_time+off0), quantize(start_time+off1)))

    # ---- future/committed helpers ----
    def _next_page_future(self, die:int, block:int)->int:
        last = self.addr_state_future[(die,block)]
        return (last + 1) if last < self.pages_per_block-1 else self.pages_per_block

    def _find_block_for_page_future_on_plane(self, die:int, plane:int, target_page:int)->Optional[int]:
        for b in self.iter_blocks_of_plane(plane):
            if self._next_page_future(die,b) == target_page:
                return b
        return None

    def _first_erased_block_on_plane(self, die:int, plane:int)->Optional[int]:
        for b in self.iter_blocks_of_plane(plane):
            if self.addr_state_future[(die,b)] == -1:
                return b
        return None

    def _committed_pages_on_plane(self, die:int, plane:int)->Set[int]:
        return {p for (b,p) in self.programmed_committed[(die,)] if (b % self.planes)==plane}

    # ---- planner (non-greedy; degrade fanout; READ=committed, PROGRAM=future) ----
    def _random_plane_sets(self, fanout:int, tries:int, start_plane:int)->List[List[int]]:
        P=list(range(self.planes)); out=[]
        for _ in range(tries):
            cand=set(random.sample(P, min(fanout,len(P))))
            if start_plane not in cand and random.random()<0.6:
                if len(cand)>0: cand.pop()
                cand.add(start_plane)
            if len(cand)==fanout:
                key=tuple(sorted(cand))
                if key not in out: out.append(key)
        return [list(ps) for ps in dict.fromkeys(out)]

    def plan_multipane(self, kind: OpKind, die:int, start_plane:int, desired_fanout:int, interleave:bool)\
            -> Optional[Tuple[List[Address], List[int], Scope]]:
        if desired_fanout<1: desired_fanout=1
        tries=self.cfg["policy"]["planner_max_tries"]
        for f in range(desired_fanout, 0, -1):
            for plane_set in self._random_plane_sets(f, tries, start_plane):
                if kind==OpKind.READ:
                    commons=None
                    for pl in plane_set:
                        pages=self._committed_pages_on_plane(die, pl)
                        commons=pages if commons is None else (commons & pages)
                        if not commons: break
                    if not commons: continue
                    page=random.choice(sorted(list(commons)))
                    targets=[]
                    for pl in plane_set:
                        blks=[b for (b,p) in self.programmed_committed[(die,)] if p==page and (b%self.planes)==pl]
                        if not blks: targets=[]; break
                        targets.append(Address(die,pl,blks[0],page))
                    if not targets: continue
                    return targets, plane_set, Scope.PLANE_SET

                elif kind==OpKind.PROGRAM:
                    # Choose candidate pages: mode of next pages across write heads; or 0 if erased exists
                    nxts=[]
                    for pl in plane_set:
                        b_head=self.write_head[(die,pl)]
                        nx=self._next_page_future(die,b_head)
                        nxts.append(None if nx>=self.pages_per_block else nx)
                    candidates=[]
                    vals=[x for x in nxts if x is not None]
                    if vals:
                        freq={}
                        for v in vals: freq[v]=freq.get(v,0)+1
                        candidates.append(max(freq.items(), key=lambda x:x[1])[0])
                    if all(self._first_erased_block_on_plane(die,pl) is not None for pl in plane_set):
                        if 0 not in candidates: candidates.append(0)
                    if not candidates: continue

                    chosen=None
                    for page in candidates:
                        tlist=[]
                        ok=True
                        for pl in plane_set:
                            b_head=self.write_head[(die,pl)]
                            if self._next_page_future(die,b_head)==page:
                                b=b_head
                            else:
                                b=self._find_block_for_page_future_on_plane(die,pl,page)
                                if b is None: ok=False; break
                            tlist.append(Address(die,pl,b,page))
                        if ok: chosen=tlist; break
                    if not chosen: continue
                    return chosen, plane_set, Scope.DIE_WIDE

                elif kind==OpKind.ERASE:
                    targets=[]
                    for pl in plane_set:
                        b=self.write_head[(die,pl)]
                        if self.addr_state_future[(die,b)] == -1:
                            # pick a non-erased block on this plane if possible
                            found=None
                            for bb in self.iter_blocks_of_plane(pl):
                                if self.addr_state_future[(die,bb)] >= 0:
                                    found=bb; break
                            if found is not None: b=found
                    targets.append(Address(die, pl, b, 0))  # ERASE도 page=0
                    return [Address(die, plane_set[0], plane_set[0], 0)], plane_set[:1], Scope.NONE  # SR도 page=0

                elif kind==OpKind.SR:
                    return [Address(die, plane_set[0], plane_set[0], None)], plane_set[:1], Scope.NONE

        return None

    # ---- precheck/reserve/future/commit ----
    def precheck_planescope(self, kind: OpKind, targets: List[Address], start_hint: float, scope: Scope)->bool:
        start_hint=quantize(start_hint); end_hint=start_hint
        die=targets[0].die
        # time overlap check
        if scope==Scope.DIE_WIDE: planes={(die,p) for p in range(self.planes)}
        elif scope==Scope.PLANE_SET: planes={(t.die,t.plane) for t in targets}
        else: planes={(targets[0].die, targets[0].plane)}
        for (d,p) in planes:
            for (s,e,_) in self.resv[(d,p)]:
                if not (end_hint<=s or e<=start_hint): return False
        # address/plane consistency + rules
        for t in targets:
            if t.plane != (t.block % self.planes): return False  # consistency
            com=self.addr_state_committed[(t.die,t.block)]
            fut=self.addr_state_future[(t.die,t.block)]
            if kind==OpKind.PROGRAM:
                if t.page is None: return False
                if t.page != fut + 1: return False
                if t.page >= self.pages_per_block: return False
            elif kind==OpKind.READ:
                if t.page is None: return False
                if (t.block, t.page) not in self.programmed_committed[(t.die,)]: return False
            elif kind==OpKind.ERASE:
                pass
        return True

    def reserve_planescope(self, op: Operation, start: float, end: float):
        die=op.targets[0].die; start=quantize(start); end=quantize(end)
        if op.meta.get("scope")=="DIE_WIDE":
            planes=[(die,p) for p in range(self.planes)]
        elif op.meta.get("scope")=="PLANE_SET":
            planes=[(t.die,t.plane) for t in op.targets]
        else:
            planes=[(op.targets[0].die, op.targets[0].plane)]
        for (d,p) in planes:
            self.available[(d,p)]=max(self.available[(d,p)], end)
            self.resv[(d,p)].append((start,end,None))

    def register_future(self, op: Operation, start: float, end: float):
        for t in op.targets:
            key=(t.die,t.block)
            if op.kind==OpKind.PROGRAM and t.page is not None:
                self.addr_state_future[key] = max(self.addr_state_future[key], t.page)
                # advance write head if full
                nxt = self._next_page_future(t.die, t.block)
                if nxt >= self.pages_per_block:
                    er = self._first_erased_block_on_plane(t.die, t.plane)
                    if er is not None:
                        self.write_head[(t.die, t.plane)] = er
            elif op.kind==OpKind.ERASE:
                self.addr_state_future[key] = -1
                self.write_head[(t.die, t.plane)] = t.block

    def commit(self, op: Operation):
        for t in op.targets:
            key=(t.die,t.block)
            if op.kind==OpKind.PROGRAM and t.page is not None:
                self.addr_state_committed[key] = max(self.addr_state_committed[key], t.page)
                self.programmed_committed[(t.die,)].add((t.block, t.page))
            elif op.kind==OpKind.ERASE:
                self.addr_state_committed[key] = -1
                self.programmed_committed[(t.die,)] = {
                    pp for pp in self.programmed_committed[(t.die,)] if pp[0] != t.block
                }

# --------------------------------------------------------------------------
# Exclusion Manager (runtime blocking windows)
@dataclass
class ExclWindow:
    start: float; end: float
    scope: str
    die: Optional[int]
    tokens: Set[str]  # {"ANY"}, {"BASE:READ"}, {"ALIAS:MUL_READ"} ...

class ExclusionManager:
    def __init__(self, cfg: Dict[str,Any]):
        self.cfg = cfg
        self.global_windows: List[ExclWindow] = []
        self.die_windows: Dict[int, List[ExclWindow]] = {}

    def _state_windows(self, op: Operation, start: float, states: List[str]) -> List[Tuple[float,float]]:
        wins=[]; t=start
        segs=[]
        for s in op.states:
            segs.append((s.name, t, t+s.dur_us)); t+=s.dur_us
        if states==["*"]:
            return [(segs[0][1], segs[-1][2])]
        want=set(states)
        for name, s0, s1 in segs:
            if name in want: wins.append((s0,s1))
        return wins

    def _token_blocks(self, tok: str, op: Operation) -> bool:
        if tok=="ANY": return True
        if tok.startswith("BASE:"):
            base=tok.split(":")[1]
            return op.kind.name==base
        if tok.startswith("ALIAS:"):
            alias=tok.split(":")[1]
            if alias=="MUL_READ": return (op.kind==OpKind.READ and op.meta.get("arity",1)>1)
            if alias=="SIN_READ": return (op.kind==OpKind.READ and op.meta.get("arity",1)==1)
        return False

    def allowed(self, op: Operation, start: float, end: float) -> bool:
        for w in self.global_windows:
            if not (end<=w.start or w.end<=start):
                if any(self._token_blocks(tok, op) for tok in w.tokens): return False
        die=op.targets[0].die
        for w in self.die_windows.get(die, []):
            if not (end<=w.start or w.end<=start):
                if any(self._token_blocks(tok, op) for tok in w.tokens): return False
        return True

    def register(self, op: Operation, start: float):
        rules = self.cfg.get("constraints",{}).get("exclusions",[])
        die = op.targets[0].die
        for r in rules:
            when=r["when"]; if_op=when.get("op")
            if if_op != op.kind.name:
                if not (op.kind==OpKind.READ and if_op=="READ"):
                    continue
            alias_need = when.get("alias")
            if alias_need=="MUL" and not (op.meta.get("arity",1)>1): continue
            if alias_need=="SIN" and not (op.meta.get("arity",1)==1): continue
            states = when.get("states", ["*"])
            windows = self._state_windows(op, start, states)
            scope=r.get("scope","GLOBAL"); tokens=set(r.get("blocks",[]))
            for (s0,s1) in windows:
                w=ExclWindow(start=quantize(s0), end=quantize(s1), scope=scope,
                             die=(die if scope=="DIE" else None), tokens=tokens)
                if scope=="GLOBAL":
                    self.global_windows.append(w)
                else:
                    self.die_windows.setdefault(die,[]).append(w)

# --------------------------------------------------------------------------
# Obligation Manager (with stats)
@dataclass(order=True)
class _ObHeapItem:
    deadline_us: float
    seq: int
    ob: "Obligation" = field(compare=False)

@dataclass
class Obligation:
    id: int
    require: OpKind
    targets: List[Address]
    deadline_us: float
    hard_slot: bool

class ObligationManager:
    def __init__(self, cfg_list: List[Dict[str,Any]]):
        self.specs = cfg_list
        self.heap: List[_ObHeapItem] = []
        self._seq = 0
        self.assigned: Dict[int, Obligation] = {}
        self.stats = {"created":0, "assigned":0, "fulfilled":0, "fulfilled_in_time":0, "expired":0}

    def on_commit(self, op: Operation, now_us: float):
        # READ completion -> create DOUT obligation(s)
        for spec in self.specs:
            if spec["issuer"] == op.kind.name:
                self._seq += 1
                dt = sample_dist(spec["window_us"])
                ob = Obligation(
                    id=self._seq,
                    require = OpKind[spec["require"]],
                    targets = op.targets,  # plane-set op as one obligation
                    deadline_us = quantize(now_us + dt),
                    hard_slot = spec["priority_boost"].get("hard_slot", False),
                )
                heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=ob.id, ob=ob))
                self.stats["created"] += 1
                first=op.targets[0]
                print(f"[{now_us:7.2f} us] OBLIG  created: {op.kind.name} -> {ob.require.name} by {ob.deadline_us:7.2f} us, target(d{first.die},p{first.plane})")

    def pop_urgent(self, now_us: float, die:int, plane:int, horizon_us: float, earliest_start: float) -> Optional[Obligation]:
        if not self.heap: return None
        kept: List[_ObHeapItem] = []
        chosen: Optional[Obligation] = None
        now_us=quantize(now_us); earliest_start=quantize(earliest_start)
        while self.heap and not chosen:
            item = heapq.heappop(self.heap)
            ob=item.ob
            plane_list={a.plane for a in ob.targets}
            same_die=(ob.targets[0].die==die); same_plane=(plane in plane_list)
            in_horizon=((ob.deadline_us - now_us) <= max(horizon_us, 0.0)) or ob.hard_slot
            feasible=(earliest_start <= ob.deadline_us)
            if same_die and same_plane and in_horizon and feasible:
                chosen=ob; break
            kept.append(item)
        for it in kept: heapq.heappush(self.heap, it)
        return chosen

    def mark_assigned(self, ob: Obligation):
        self.assigned[ob.id] = ob
        self.stats["assigned"] += 1

    def mark_fulfilled(self, ob: Obligation, now: float):
        self.assigned.pop(ob.id, None)
        self.stats["fulfilled"] += 1
        if now <= ob.deadline_us:
            self.stats["fulfilled_in_time"] += 1

    def expire_due(self, now: float):
        kept=[]
        expired=0
        while self.heap and self.heap[0].deadline_us <= now:
            heapq.heappop(self.heap); expired+=1
        self.stats["expired"] += expired

# --------------------------------------------------------------------------
# Policy Engine (phase-conditional + admission gating + backoff)
class PolicyEngine:
    def __init__(self, cfg, addr: AddressManager, obl: ObligationManager, excl: ExclusionManager):
        self.cfg=cfg; self.addr=addr; self.obl=obl; self.excl=excl
        self.stats={"alias_degrade":0}

    def _score(self, op_name: str, phase_label: str, g: Dict[str,str], l: Dict[str,str]) -> float:
        w=self.cfg["weights"]["base"]["host"].get(op_name,0.0)
        w*=self.cfg["weights"]["g_state"]["pgmable_ratio"].get(g["pgmable_ratio"],1.0)
        w*=self.cfg["weights"]["g_state"]["readable_ratio"].get(g["readable_ratio"],1.0)
        w*=self.cfg["weights"]["g_local"]["plane_busy_frac"].get(l["plane_busy_frac"],1.0)
        near="MID_NEAR"
        if phase_label.endswith("START"): near="START_NEAR"
        elif phase_label.endswith("END"): near="END_NEAR"
        w*=self.cfg["weights"]["g_phase"].get(op_name,{}).get(near,1.0)
        return w

    def _fanout_from_alias(self, base_name: str, alias_constraint: Optional[Tuple[str,int]], hook_label: str)->Tuple[int,bool]:
        fanout, interleave = get_phase_selection_override(self.cfg, hook_label, base_name)
        if alias_constraint:
            mode,val=alias_constraint
            if mode=="eq": fanout=val
            elif mode=="ge": fanout=max(fanout,val)
        return max(1,fanout), interleave

    def _exclusion_ok(self, op: Operation, die:int, plane_set: List[int], start_hint: float) -> bool:
        scope = Scope[op.meta["scope"]]
        start = self.addr.earliest_start_for_scope(die, scope, plane_set)
        dur = get_op_duration(op); end=quantize(start+dur)
        return self.excl.allowed(op, start, end)

    def _admission_ok(self, now_us: float, hook_label: str, kind_name: str, start_hint: float, deadline: Optional[float]=None) -> bool:
        adm = self.cfg.get("admission", {})
        delta = get_admission_delta(self.cfg, hook_label, kind_name)
        if deadline is not None:  # for obligations if bypass disabled
            delta = min(delta, max(0.0, deadline - now_us))
        return start_hint <= now_us + delta

    def propose(self, now_us: float, hook: PhaseHook, g: Dict[str,str], l: Dict[str,str], earliest_start: float) -> Optional[Operation]:
        die, hook_plane = hook.die, hook.plane

        # 0) obligations first (with optional admission bypass)
        ob=self.obl.pop_urgent(now_us, die, hook_plane, horizon_us=10.0, earliest_start=earliest_start)
        if ob:
            cfg_op=self.cfg["op_specs"][ob.require.name]
            op=build_operation(ob.require, cfg_op, ob.targets)
            op.meta["scope"]=cfg_op["scope"]; op.meta["plane_list"]=sorted({a.plane for a in ob.targets}); op.meta["arity"]=len(op.meta["plane_list"])
            op.meta["obligation"]=ob
            scope=Scope[op.meta["scope"]]; plane_set=op.meta["plane_list"]
            start_hint=self.addr.earliest_start_for_scope(die, scope, plane_set)
            # admission bypass?
            bypass = self.cfg.get("admission",{}).get("obligation_bypass",True)
            admission_ok = True if bypass else self._admission_ok(now_us, hook.label, ob.require.name, start_hint, ob.deadline_us)
            if admission_ok and \
               self.addr.precheck_planescope(op.kind, op.targets, start_hint, scope) and \
               self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op)) and \
               self._exclusion_ok(op, die, plane_set, start_hint):
                op.meta["source"]="obligation"; op.meta["phase_key_used"]="(obligation)"
                return op
            # else: fallthrough to policy

        # 1) phase-conditional (alias keys allowed + SR)
        allow=set(list(OP_ALIAS.keys())+["READ","PROGRAM","ERASE","SR"])
        dist, used_key = get_phase_dist(self.cfg, hook.label)
        if dist:
            pick=roulette_pick(dist, allow)
            if pick:
                base, alias_const = resolve_alias(pick)
                kind=OpKind[base]
                # plan
                if kind==OpKind.SR:
                    plan=self.addr.plan_multipane(kind, die, hook_plane, 1, True)
                else:
                    fanout, interleave=self._fanout_from_alias(base, alias_const, hook.label)
                    plan=self.addr.plan_multipane(kind, die, hook_plane, fanout, interleave)
                    if not plan and fanout>1:
                        self.stats["alias_degrade"]+=1
                        plan=self.addr.plan_multipane(kind, die, hook_plane, 1, interleave)
                if plan:
                    targets, plane_set, scope=plan
                    cfg_op=self.cfg["op_specs"][base]; op=build_operation(kind, cfg_op, targets)
                    op.meta["scope"]=cfg_op["scope"]; op.meta["plane_list"]=plane_set; op.meta["arity"]=len(plane_set); op.meta["alias_used"]=pick
                    start_hint=self.addr.earliest_start_for_scope(die, scope, plane_set)
                    # admission gating: near-future only
                    if not self._admission_ok(now_us, hook.label, base, start_hint):
                        return None
                    if self.addr.precheck_planescope(kind, targets, start_hint, scope) and \
                       self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op)) and \
                       self._exclusion_ok(op, die, plane_set, start_hint):
                        op.meta["source"]="policy.phase_conditional"; op.meta["phase_key_used"]=used_key; return op
            # else fallthrough to backoff

        # 2) backoff (READ/PROGRAM/ERASE/SR)
        cand=[]
        for name in ["READ","PROGRAM","ERASE","SR"]:
            s=self._score(name, hook.label, g, l)
            if s>0: cand.append((name,s))
        if not cand: return None
        tot=sum(s for _,s in cand); r=random.random()*tot; acc=0.0; pick=cand[-1][0]
        for name,s in cand:
            acc+=s
            if r<=acc: pick=name; break
        kind=OpKind[pick]
        if kind==OpKind.SR:
            plan=self.addr.plan_multipane(kind, die, hook_plane, 1, True)
        else:
            fanout, interleave=get_phase_selection_override(self.cfg, hook.label, pick)
            plan=self.addr.plan_multipane(kind, die, hook_plane, fanout, interleave)
        if not plan: return None
        targets, plane_set, scope=plan
        cfg_op=self.cfg["op_specs"][pick]; op=build_operation(kind, cfg_op, targets)
        op.meta["scope"]=cfg_op["scope"]; op.meta["plane_list"]=plane_set; op.meta["arity"]=len(plane_set)
        start_hint=self.addr.earliest_start_for_scope(die, scope, plane_set)
        # admission gating
        if not self._admission_ok(now_us, hook.label, pick, start_hint):
            return None
        if not (self.addr.precheck_planescope(kind, targets, start_hint, scope) and \
                self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op)) and \
                self._exclusion_ok(op, die, plane_set, start_hint)):
            return None
        op.meta["source"]="policy.score_backoff"; op.meta["phase_key_used"]="(score_backoff)"; return op

# --------------------------------------------------------------------------
# Selection overrides
def get_phase_selection_override(cfg: Dict[str,Any], hook_label: str, kind_name: str):
    op, state, pos = parse_hook_key(hook_label)
    po=cfg.get("selection",{}).get("phase_overrides",{})
    keys=[]
    if op and state and pos: keys.append(f"{op}.{state}.{pos}")
    if op and state:         keys.append(f"{op}.{state}")
    for k in keys:
        val=po.get(k)
        if val:
            return int(val.get("fanout",1)), bool(val.get("interleave",True))
    dflt=cfg.get("selection",{}).get("defaults",{}).get(kind_name,{"fanout":1,"interleave":True})
    return int(dflt.get("fanout",1)), bool(dflt.get("interleave",True))

# --------------------------------------------------------------------------
# Scheduler
def _addr_str(a: Address)->str: return f"(d{a.die},p{a.plane},b{a.block},pg{a.page})"

class Scheduler:
    def __init__(self, cfg, addr, spe, obl, excl, logger: Optional[TimelineLogger]=None):
        self.cfg=cfg; self.addr=addr; self.SPE=spe; self.obl=obl; self.excl=excl
        self.now=0.0; self.ev=[]; self._seq=0
        self.stat_propose_calls=0; self.stat_scheduled=0
        self.logger = logger or TimelineLogger()
        self._push(0.0, "QUEUE_REFILL", None)
        for plane in range(self.addr.planes):
            self._push(0.0, "PHASE_HOOK", PhaseHook(0.0, "BOOT.START", 0, plane))

    def _push(self, t: float, typ: str, payload: Any):
        heapq.heappush(self.ev, (quantize(t), self._seq, typ, payload)); self._seq+=1

    def _start_time_for_op(self, op: Operation) -> float:
        die=op.targets[0].die
        scope=Scope[op.meta.get("scope","PLANE_SET")]
        plane_set=[a.plane for a in op.targets]
        t_planes=self.addr.earliest_start_for_scope(die, scope, plane_set)
        return quantize(max(self.now, t_planes))

    def _label_for_read(self, op: Operation)->str:
        if op.kind!=OpKind.READ: return op.kind.name
        return "MUL_READ" if op.meta.get("arity",1)>1 else "SIN_READ"

    def _schedule_operation(self, op: Operation):
        start=self._start_time_for_op(op); dur=get_op_duration(op); end=quantize(start+dur)
        # reserve plane scope + bus + register exclusions + future update
        self.addr.reserve_planescope(op, start, end)
        self.addr.bus_reserve(start, self.addr.bus_segments_for_op(op))
        self.excl.register(op, start)
        self.addr.register_future(op, start, end)
        if self.logger is not None:
            self.logger.log_op(op, start, end, label_for_read=self._label_for_read(op))

        # obligation assignment stats
        if "obligation" in op.meta:
            self.obl.mark_assigned(op.meta["obligation"])

        # push events
        self._push(start, "OP_START", op); self._push(end, "OP_END", op)

        # hooks (READ → SIN_READ/MUL_READ label)
        label_op=self._label_for_read(op)
        for t in op.targets:
            cur=start
            for s in op.states:
                self._push(cur,               "PHASE_HOOK", PhaseHook(cur,               f"{label_op}.{s.name}.START", t.die, t.plane))
                if s.name=="CORE_BUSY":
                    self._push(cur + s.dur_us*0.5, "PHASE_HOOK", PhaseHook(cur + s.dur_us*0.5, f"{label_op}.{s.name}.MID",   t.die, t.plane))
                self._push(cur + s.dur_us,    "PHASE_HOOK", PhaseHook(cur + s.dur_us,    f"{label_op}.{s.name}.END",   t.die, t.plane))
                cur += s.dur_us

        first=op.targets[0]
        print(f"[{self.now:7.2f} us] SCHED  {op.kind.name:7s} arity={op.meta.get('arity')} scope={op.meta.get('scope')} start={start:7.2f} end={end:7.2f} 1st={_addr_str(first)} src={op.meta.get('source')} alias={op.meta.get('alias_used')}")

        self.stat_scheduled+=1

    def run_until(self, t_end: float):
        t_end=quantize(t_end)
        while self.ev and self.ev[0][0] <= t_end:
            self.now, _, typ, payload = heapq.heappop(self.ev)

            # expire obligations due
            self.obl.expire_due(self.now)

            if typ=="QUEUE_REFILL":
                for plane in range(self.addr.planes):
                    self._push(self.now, "PHASE_HOOK", PhaseHook(self.now, "REFILL.NUDGE", 0, plane))
                self._push(self.now + self.cfg["policy"]["queue_refill_period_us"], "QUEUE_REFILL", None)

            elif typ=="PHASE_HOOK":
                hook: PhaseHook = payload
                earliest_start=self.addr.available_at(hook.die, hook.plane)
                g,l=self.addr.observe_states(hook.die, hook.plane, self.now)
                self.stat_propose_calls+=1
                op=self.SPE.propose(self.now, hook, g, l, earliest_start)
                if op: self._schedule_operation(op)

            elif typ=="OP_START":
                op: Operation=payload; first=op.targets[0]
                print(f"[{self.now:7.2f} us] START  {op.kind.name:7s} arity={op.meta.get('arity')} target={_addr_str(first)}")

            elif typ=="OP_END":
                op: Operation=payload; first=op.targets[0]
                print(f"[{self.now:7.2f} us] END    {op.kind.name:7s} arity={op.meta.get('arity')} target={_addr_str(first)}")
                self.addr.commit(op)
                # obligation fulfillment stats
                if "obligation" in op.meta:
                    self.obl.mark_fulfilled(op.meta["obligation"], self.now)
                self.obl.on_commit(op, self.now)

        # final stats
        print(f"\n=== Stats ===")
        print(f"propose calls : {self.stat_propose_calls}")
        print(f"scheduled ops : {self.stat_scheduled}")
        if self.stat_propose_calls:
            print(f"accept ratio  : {100.0*self.stat_scheduled/self.stat_propose_calls:.1f}%")
        s=self.obl.stats
        rate=(100.0*s["fulfilled_in_time"]/s["created"]) if s["created"] else 0.0
        print(f"obligations   : created={s['created']} assigned={s['assigned']} fulfilled={s['fulfilled']} in_time={s['fulfilled_in_time']} expired={s['expired']} success={rate:.1f}%")

# --------------------------------------------------------------------------
# Main
def main():
# 1) 구성
    logger = TimelineLogger()
    addr = AddressManager(CFG); excl = ExclusionManager(CFG)
    obl  = ObligationManager(CFG["obligations"])
    spe  = PolicyEngine(CFG, addr, obl, excl)
    sch  = Scheduler(CFG, addr, spe, obl, excl, logger=logger)

    # 2) 실행
    sch.run_until(CFG["policy"]["run_until_us"])

    # 3) DataFrame
    df = logger.to_dataframe()
    df.to_csv("nand_timeline.csv", index=False)

    # 4) 시각화
    plot_gantt(df, die=0, title="Die 0 Gantt")
    plot_gantt_by_die(df)  # 모든 die별로 개별 그림

    plot_block_page_sequence_3d(df, die=0, kinds=("ERASE","PROGRAM","READ"),
                                z_mode="per_block", draw_lines=True,
                                title="Die 0 Block-Page-Order (per-block order)")
    plot_block_page_sequence_3d_by_die(df, kinds=("ERASE","PROGRAM","READ"),
                                    z_mode="global_die", draw_lines=True)

    # 5) 규칙 자동검증
    report = validate_timeline(df, CFG)
    print_validation_report(report, max_rows=30)
    viol_df = violations_to_dataframe(report)
    viol_df.to_csv("nand_violations.csv", index=False)
if __name__=="__main__":
    main()