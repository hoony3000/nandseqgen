# nandsim_p6_candidate_start_fix.py
# - P6: candidate_start 기반 검사 + schedule 직전 fail-safe
# - Admission/exclusion 설계와 정합성 유지(near-future만 수용, 미래예약 폭주 억제 강화)
# - Latch/DOUT 중 SR 예약 금지 케이스를 bus/excl에서 일관되게 차단

from __future__ import annotations
import heapq, random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import defaultdict
import csv
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
# --- Patches: RNG seed + fixed-duration enforcement for validate_timeline ---
def _seed_rng_from_cfg(cfg):
    import random
    try:
        seed = cfg.get("rng_seed", None)
        if seed is not None:
            random.seed(int(seed))
            print(f"[INIT] random.seed({seed})")
            try:
                import numpy as np  # optional
                np.random.seed(int(seed))
                print(f"[INIT] numpy.random.seed({seed})")
            except Exception:
                pass
    except Exception as e:
        print(f"[INIT] random.seed skipped: {e}")

def _coerce_states_to_fixed(cfg):
    """
    Ensure all op_specs.states use fixed durations; if not, convert using representative stats.
    - normal: use mean
    - exp   : use 1/lambda as mean
    """
    converted = 0
    for op, spec in cfg.get("op_specs", {}).items():
        for st in spec.get("states", []):
            dist = st.get("dist", {})
            kind = dist.get("kind")
            if kind != "fixed":
                val = None
                if kind == "normal":
                    val = float(dist.get("mean", 0.0))
                elif kind == "exp":
                    lam = float(dist.get("lambda", 1.0))
                    val = 1.0/lam if lam > 0 else 0.0
                elif "value" in dist:
                    val = float(dist["value"])
                else:
                    try:
                        val = float(dist.get("mean", 0.0))
                    except Exception:
                        val = 0.0
                st["dist"] = {"kind":"fixed", "value": float(val)}
                converted += 1
    if converted:
        print(f"[INIT] coerced {converted} state dists to fixed for validation")

def _validate_phase_conditional_cfg(cfg):
    """
    Ensure each phase_conditional distribution sums to 1.0 (within epsilon) and has no negative weights.
    This is a pre-run guard to avoid unstable sampling behavior.
    """
    pc = cfg.get("phase_conditional", {})
    eps = 1e-6
    for key, dist in pc.items():
        if not isinstance(dist, dict):
            continue
        # OP.PHASE.POS 형태 금지 (키 2개 이상의 점 포함 금지)
        if key.count(".") >= 2 and key != "DEFAULT":
            raise ValueError(f"phase_conditional key not supported (use OP.PHASE only): {key}")
        vals = list(dist.values())
        if any((float(v) < 0.0) for v in vals):
            raise ValueError(f"phase_conditional[{key}] contains negative weight(s)")
        s = float(sum(float(v) for v in vals))
        if abs(s - 1.0) > eps:
            raise ValueError(f"phase_conditional[{key}] sum={s} != 1.0")

CFG = {
    "rng_seed": 12345,
    "policy": {
        "queue_refill_period_us": 50.0,
        "run_until_us": 10000.0,
        "planner_max_tries": 8,
    },

    # Admission gating (near-future only to prevent policy explosion)
    "admission": {
        "default_delta_us": 0.30,
        "op_overrides": {
            "SR": 0.30
        },
        "obligation_bypass": True  # obligations ignore admission delta by default
    },

    # Phase-conditional proposal (alias keys allowed)
    "phase_conditional": {
        "READ.ISSUE":           {"MUL_PROGRAM": 0.15, "SIN_PROGRAM": 0.10, "SIN_READ": 0.35, "MUL_READ": 0.25, "SIN_ERASE": 0.15},
        "PROGRAM.ISSUE":        {"SIN_READ": 0.50, "MUL_READ": 0.20, "SIN_PROGRAM": 0.15, "SIN_ERASE": 0.10, "SR": 0.05},
        "ERASE.ISSUE":          {"SIN_PROGRAM": 0.40, "MUL_PROGRAM": 0.20, "SIN_READ": 0.25, "MUL_READ": 0.10, "SR": 0.05},
        "DEFAULT":              {"SIN_READ": 0.55, "SIN_PROGRAM": 0.25, "SIN_ERASE": 0.10, "SR": 0.10}
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
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "fixed", "value": 8.0}},
                {"name": "DATA_OUT",  "bus": False, "dist": {"kind": "fixed", "value": 2.0}},
            ],
        },
        "PROGRAM": {
            "scope": "DIE_WIDE",
            "page_equal_required": True,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed",  "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "fixed", "value": 20.0}},
            ],
        },
        "ERASE": {
            "scope": "DIE_WIDE",
            "page_equal_required": False,
            "states": [
                {"name": "ISSUE",     "bus": True,  "dist": {"kind": "fixed",  "value": 0.4}},
                {"name": "CORE_BUSY", "bus": False, "dist": {"kind": "fixed", "value": 40.0}},
            ],
        },
        "DOUT": {
            "scope": "NONE",
            "page_equal_required": True,
            "states": [
                {"name": "ISSUE",     "bus": True, "dist": {"kind": "fixed",  "value": 0.2}},
                {"name": "DATA_OUT",  "bus": True, "dist": {"kind": "fixed", "value": 1.0}},
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
            # "window_us": {"kind": "normal", "mean": 6.0, "std": 1.5, "min": 3.0},
            "window_us": {"kind": "fixed", "value": 20.0},
            "priority_boost": {"start_us_before_deadline": 2.5, "boost_factor": 2.0, "hard_slot": True},
        }
    ],

    # Topology (redefined): plane = block % planes
    "topology": {
        "dies": 2,
        "planes": 4,
        "blocks": 32,           # total blocks per die (not per plane)
        "pages_per_block": 40,
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
# Rejection logging (per-attempt structured log + aggregated stats)
@dataclass
class RejectEvent:
    now_us: float
    die: int
    plane: int
    hook: str
    stage: str                 # 'obligation' | 'phase_conditional' | 'backoff'
    attempted: Optional[str]   # 'READ'/'PROGRAM'/... or None
    alias: Optional[str]       # 'SIN_READ'/'MUL_READ'/... or None
    fanout: Optional[int]
    plane_set: Optional[str]   # e.g. "[0,2]" stringified
    reason: str                # 'admission'/'plan_none'/...
    detail: str                # free-form short note
    earliest_start: Optional[float] = None
    admission_delta: Optional[float] = None

class RejectionLogger:
    def __init__(self):
        self.rows: List[RejectEvent] = []
        # nested counts: stage -> reason -> count
        self.stats = defaultdict(lambda: defaultdict(int))
        # stage-wise attempts/accepts
        self.stage_attempts = defaultdict(int)
        self.stage_accepts  = defaultdict(int)

    def log_attempt(self, stage: str):
        self.stage_attempts[stage] += 1

    def log_accept(self, stage: str):
        self.stage_accepts[stage] += 1

    def log_reject(self, ev: RejectEvent):
        self.rows.append(ev)
        self.stats[ev.stage][ev.reason] += 1

    def to_csv(self, path: str = "reject_log.csv"):
        if not self.rows:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[ "now_us","die","plane","hook","stage","attempted","alias",
                             "fanout","plane_set","reason","detail",
                             "earliest_start","admission_delta" ]
            )
            w.writeheader()
            for r in self.rows:
                w.writerow({
                    "now_us": r.now_us,
                    "die": r.die,
                    "plane": r.plane,
                    "hook": r.hook,
                    "stage": r.stage,
                    "attempted": r.attempted,
                    "alias": r.alias,
                    "fanout": r.fanout,
                    "plane_set": r.plane_set,
                    "reason": r.reason,
                    "detail": r.detail,
                    "earliest_start": r.earliest_start,
                    "admission_delta": r.admission_delta,
                })

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
    # POS granularity removed by plan; only OP.STATE and DEFAULT are considered
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
    adm = cfg.get("admission", {})
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

    def candidate_start_for_scope(self, now_us: float, die:int, scope: Scope, plane_set: Optional[List[int]]=None)->float:
        """실제 스케줄러가 사용할 시작 후보시각 = max(now, earliest_start_for_scope)."""
        t_planes = self.earliest_start_for_scope(die, scope, plane_set)
        return quantize(max(now_us, t_planes))

    def observe_states(self, die:int, plane:int, now_us: float):
        pgmable_blocks=0; readable_blocks=0
        cnt=0
        for b in self.iter_blocks_of_plane(plane):
            cnt += 1
            fut=self.addr_state_future[(die,b)]
            com=self.addr_state_committed[(die,b)]
            if fut < self.pages_per_block-1: pgmable_blocks += 1
            if com >= 0: readable_blocks += 1
        total=max(1, cnt)
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
        """난수 기반 plane-set 후보 생성 (재현성은 random.seed에 따름)"""
        P=list(range(self.planes)); out=[]
        for _ in range(tries):
            cand=set(random.sample(P, min(fanout,len(P))))
            if start_plane not in cand and random.random()<0.6:
                # 무작위로 하나 제거하여 start_plane 포함
                if len(cand)>0:
                    rem=random.choice(sorted(cand))
                    cand.remove(rem)
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
                    # candidate page: mode of next pages across write heads; or 0 if fresh erased exists
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
                        # prefer a non-erased block on this plane; fallback to write_head
                        chosen_b=None
                        for bb in self.iter_blocks_of_plane(pl):
                            if self.addr_state_future[(die,bb)] >= 0:
                                chosen_b=bb; break
                        if chosen_b is None:
                            chosen_b=self.write_head[(die,pl)]
                        targets.append(Address(die, pl, chosen_b, 0))  # page=0 for logging
                    return targets, plane_set, Scope.DIE_WIDE

                elif kind==OpKind.SR:
                    # page=0 for uniform address shape
                    return [Address(die, plane_set[0], plane_set[0], 0)], plane_set[:1], Scope.NONE

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
# Latch Manager: READ.end_us ~ DOUT.end_us 동안 (die,plane) 래치 보존 락
@dataclass
class _Latch:
    start_us: float
    end_us: Optional[float]  # None -> open until DOUT end

class LatchManager:
    def __init__(self):
        # (die, plane) -> _Latch
        self._locks: Dict[Tuple[int,int], _Latch] = {}

    def plan_lock_after_read(self, targets: List[Address], read_end_us: float):
        for t in targets:
            self._locks[(t.die, t.plane)] = _Latch(start_us=read_end_us, end_us=None)

    def release_on_dout_end(self, targets: List[Address], now_us: float):
        for t in targets:
            key=(t.die, t.plane)
            if key in self._locks:
                del self._locks[key]

    def _is_locked_at(self, die:int, plane:int, t0:float) -> bool:
        lock = self._locks.get((die,plane))
        if not lock: return False
        if t0 < lock.start_us: return False
        if lock.end_us is None: return True
        return t0 < lock.end_us

    def allowed(self, op: Operation, start_hint: float) -> bool:
        # DOUT/SR allowed (does not corrupt latch)
        if op.kind in (OpKind.DOUT, OpKind.SR):
            return True
        if op.kind not in (OpKind.READ, OpKind.PROGRAM, OpKind.ERASE):
            return True

        die = op.targets[0].die
        scope = op.meta.get("scope", "PLANE_SET")

        if scope == "DIE_WIDE":
            for (d,p), _ in self._locks.items():
                if d == die and self._is_locked_at(d, p, start_hint):
                    return False
            return True
        else:
            for t in op.targets:
                if self._is_locked_at(t.die, t.plane, start_hint):
                    return False
            return True

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
                # Bootstrap 체인으로 미리 생성된 DOUT과 중복 생성 방지 가드
                if op.meta.get("source") == "bootstrap" or op.meta.get("skip_dout_creation"):
                    continue

                dt = sample_dist(spec["window_us"])
                base_deadline = quantize(now_us + dt)
                hard_slot = spec.get("priority_boost", {}).get("hard_slot", False)
                plane_stagger = spec.get("priority_boost", {}).get("plane_stagger_us", 0.2)

                # 멀티플레인 READ의 경우 plane 순서대로 DOUT을 분산 생성
                if op.kind == OpKind.READ and len(op.targets) > 1:
                    sorted_targets = sorted(op.targets, key=lambda a: a.plane)
                    for idx, t in enumerate(sorted_targets):
                        self._seq += 1
                        ob = Obligation(
                            id=self._seq,
                            require=OpKind[spec["require"]],
                            targets=[t],
                            deadline_us=quantize(base_deadline + idx * plane_stagger),
                            hard_slot=hard_slot,
                        )
                        heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=ob.id, ob=ob))
                        self.stats["created"] += 1
                        print(f"[{now_us:7.2f} us] OBLIG  created: READ -> {ob.require.name} by {ob.deadline_us:7.2f} us, target(d{t.die},p{t.plane})")
                else:
                    # 단일 plane 또는 비-READ 발행자의 경우 기존 방식 유지
                    self._seq += 1
                    ob = Obligation(
                        id=self._seq,
                        require=OpKind[spec["require"]],
                        targets=op.targets,
                        deadline_us=base_deadline,
                        hard_slot=hard_slot,
                    )
                    heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=ob.id, ob=ob))
                    self.stats["created"] += 1
                    first = op.targets[0]
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
        else:
            print(f"not_fulfilled: {ob.require.name} by {now:7.2f} us, deadline={ob.deadline_us:7.2f} us, target(d{ob.targets[0].die},p{ob.targets[0].plane})")

    def expire_due(self, now: float):
        kept=[]
        expired=0
        while self.heap and self.heap[0].deadline_us <= now:
            heapq.heappop(self.heap); expired+=1
        self.stats["expired"] += expired

# --------------------------------------------------------------------------
# Policy Engine (phase-conditional + admission gating + backoff + latch check)
class PolicyEngine:
    def _reject(self, now_us: float, hook: PhaseHook, stage: str, reason: str,
                attempted: Optional[str], alias: Optional[str],
                fanout: Optional[int], plane_set: Optional[List[int]],
                earliest_start: Optional[float], admission_delta: Optional[float],
                detail: str = ""):
        if self.rejlog is None:
            return
        self.rejlog.log_reject(RejectEvent(
            now_us=now_us, die=hook.die, plane=hook.plane, hook=hook.label,
            stage=stage, attempted=attempted, alias=alias, fanout=fanout,
            plane_set=(str(sorted(plane_set)) if plane_set is not None else None),
            reason=reason, detail=detail,
            earliest_start=earliest_start, admission_delta=admission_delta
        ))

    def __init__(self, cfg, addr: AddressManager, obl: ObligationManager, excl: ExclusionManager,
                 rejlog: Optional[RejectionLogger] = None,
                 latch: Optional[LatchManager] = None):
        self.cfg=cfg; self.addr=addr; self.obl=obl; self.excl=excl
        self.stats={"alias_degrade":0}
        self.rejlog = rejlog or RejectionLogger()
        self.latch = latch or LatchManager()

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

    def _exclusion_ok(self, op: Operation, start_hint: float) -> bool:
        dur = get_op_duration(op)
        end = quantize(start_hint + dur)
        return self.excl.allowed(op, start_hint, end)

    def _admission_ok(self, now_us: float, hook_label: str, kind_name: str, start_hint: float, deadline: Optional[float]=None) -> bool:
        adm = self.cfg.get("admission", {})
        delta = get_admission_delta(self.cfg, hook_label, kind_name)
        if deadline is not None:  # for obligations if bypass disabled
            delta = min(delta, max(0.0, deadline - now_us))
        return start_hint <= now_us + delta

    def propose(self, now_us: float, hook: PhaseHook, g: Dict[str,str], l: Dict[str,str], earliest_start: float) -> Optional[Operation]:
        die, hook_plane = hook.die, hook.plane

        # 0) obligations first
        stage = "obligation"
        self.rejlog.log_attempt(stage)
        ob=self.obl.pop_urgent(now_us, die, hook_plane, horizon_us=10.0, earliest_start=earliest_start)
        if ob:
            cfg_op=self.cfg["op_specs"][ob.require.name]
            op=build_operation(ob.require, cfg_op, ob.targets)
            op.meta["scope"]=cfg_op["scope"]; op.meta["plane_list"]=sorted({a.plane for a in ob.targets}); op.meta["arity"]=len(op.meta["plane_list"])
            op.meta["obligation"]=ob
            scope=Scope[op.meta["scope"]]; plane_set=op.meta["plane_list"]
            start_hint=self.addr.candidate_start_for_scope(now_us, die, scope, plane_set)

            bypass = self.cfg.get("admission",{}).get("obligation_bypass",True)
            admission_delta = (None if bypass else get_admission_delta(self.cfg, hook.label, ob.require.name))
            admission_ok = True if bypass else self._admission_ok(now_us, hook.label, ob.require.name, start_hint, ob.deadline_us)

            if not admission_ok:
                self._reject(now_us, hook, stage, "admission", ob.require.name, None, len(plane_set), plane_set, start_hint, admission_delta, "deadline_window")
            elif not self.addr.precheck_planescope(op.kind, op.targets, start_hint, scope):
                self._reject(now_us, hook, stage, "precheck", ob.require.name, None, len(plane_set), plane_set, start_hint, admission_delta, "addr/precheck")
            elif not self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op)):
                self._reject(now_us, hook, stage, "bus", ob.require.name, None, len(plane_set), plane_set, start_hint, admission_delta, "bus_conflict")
            elif not self.latch.allowed(op, start_hint):
                self._reject(now_us, hook, stage, "latch", ob.require.name, None, len(plane_set), plane_set, start_hint, admission_delta, "read->dout plane latched")
            elif not self._exclusion_ok(op, start_hint):
                self._reject(now_us, hook, stage, "excl", ob.require.name, None, len(plane_set), plane_set, start_hint, admission_delta, "exclusion_window")
            else:
                # ACCEPT
                self.rejlog.log_accept(stage)
                op.meta["source"]="obligation"; op.meta["phase_key_used"]="(obligation)"
                return op
        else:
            # no obligation
            self._reject(now_us, hook, stage, "none_available", None, None, None, None, earliest_start, None, "no_obligation")

        # 1) phase-conditional
        stage = "phase_conditional"
        self.rejlog.log_attempt(stage)
        allow=set(list(OP_ALIAS.keys())+["READ","PROGRAM","ERASE","SR"])
        dist, used_key = get_phase_dist(self.cfg, hook.label)
        if not dist:
            self._reject(now_us, hook, stage, "none_available", None, None, None, None, earliest_start, None, "no_dist_for_hook")
        else:
            pick=roulette_pick(dist, allow)
            if not pick:
                self._reject(now_us, hook, stage, "none_available", None, None, None, None, earliest_start, None, "roulette_zero_weight")
            else:
                base, alias_const = resolve_alias(pick)
                kind=OpKind[base]
                if kind==OpKind.SR:
                    plan=self.addr.plan_multipane(kind, die, hook_plane, 1, True)
                    fanout=1; alias_used="SR"
                else:
                    fanout, interleave=self._fanout_from_alias(base, alias_const, hook.label)
                    plan=self.addr.plan_multipane(kind, die, hook_plane, fanout, interleave)
                    alias_used=pick
                    if not plan and fanout>1:
                        self.stats["alias_degrade"]+=1
                        plan=self.addr.plan_multipane(kind, die, hook_plane, 1, interleave)
                        if plan: fanout=1
                if not plan:
                    self._reject(now_us, hook, stage, "plan_none", base, alias_used, fanout, None, earliest_start, None, "no_targets")
                else:
                    targets, plane_set, scope=plan
                    cfg_op=self.cfg["op_specs"][base]; op=build_operation(kind, cfg_op, targets)
                    op.meta["scope"]=cfg_op["scope"]; op.meta["plane_list"]=plane_set; op.meta["arity"]=len(plane_set); op.meta["alias_used"]=pick
                    start_hint=self.addr.candidate_start_for_scope(now_us, die, scope, plane_set)
                    adm_ok=self._admission_ok(now_us, hook.label, base, start_hint)
                    adm_delta=get_admission_delta(self.cfg, hook.label, base)
                    if not adm_ok:
                        self._reject(now_us, hook, stage, "admission", base, alias_used, len(plane_set), plane_set, start_hint, adm_delta, "near_future_gate")
                    elif not self.addr.precheck_planescope(kind, targets, start_hint, scope):
                        self._reject(now_us, hook, stage, "precheck", base, alias_used, len(plane_set), plane_set, start_hint, adm_delta, "addr/precheck")
                    elif not self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op)):
                        self._reject(now_us, hook, stage, "bus", base, alias_used, len(plane_set), plane_set, start_hint, adm_delta, "bus_conflict")
                    elif not self.latch.allowed(op, start_hint):
                        self._reject(now_us, hook, stage, "latch", base, alias_used, len(plane_set), plane_set, start_hint, adm_delta, "read->dout plane latched")
                    elif not self._exclusion_ok(op, start_hint):
                        self._reject(now_us, hook, stage, "excl", base, alias_used, len(plane_set), plane_set, start_hint, adm_delta, "exclusion_window")
                    else:
                        # ACCEPT
                        self.rejlog.log_accept(stage)
                        op.meta["source"]="policy.phase_conditional"; op.meta["phase_key_used"]=used_key
                        return op

        # backoff 단계 제거됨 (충돌 정합성 해소 계획에 따라 비활성화)
        return None

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
def _addr_str(a: Address)->str: return f"(d{a.die},p{a.plane},b{a.block},pg{a.page})"

class Scheduler:
    def __init__(self, cfg, addr:AddressManager, spe:PolicyEngine, obl:ObligationManager,
                 excl:ExclusionManager, logger: Optional[TimelineLogger]=None,
                 latch: Optional[LatchManager]=None):
        self.cfg=cfg; self.addr=addr; self.SPE=spe; self.obl=obl; self.excl=excl
        self.now=0.0; self.ev=[]; self._seq=0
        self.stat_propose_calls=0; self.stat_scheduled=0
        self.logger = logger or TimelineLogger()
        self.latch = latch or LatchManager()
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
        arity = op.meta.get("arity", 1)
        if op.kind == OpKind.READ:
            return "MUL_READ" if arity>1 else "SIN_READ"
        if op.kind == OpKind.PROGRAM:
            return "MUL_PROGRAM" if arity>1 else "SIN_PROGRAM"
        if op.kind == OpKind.ERASE:
            return "MUL_ERASE" if arity>1 else "SIN_ERASE"
        return op.kind.name

    def _schedule_operation(self, op: Operation):
        start=self._start_time_for_op(op); dur=get_op_duration(op); end=quantize(start+dur)

        # ---- fail-safe: schedule 직전 마지막 충돌 점검 ----
        segs = self.addr.bus_segments_for_op(op)
        if not self.addr.bus_precheck(start, segs):
            print(f"[WARN] BUS conflict at schedule-time: {op.kind.name} start={start:.2f} end={end:.2f}")
            return
        if not self.latch.allowed(op, start):
            print(f"[WARN] LATCH conflict at schedule-time: {op.kind.name} start={start:.2f} end={end:.2f}")
            return
        if not self.excl.allowed(op, start, end):
            print(f"[WARN] EXCLUSION conflict at schedule-time: {op.kind.name} start={start:.2f} end={end:.2f}")
            return
        # -----------------------------------------------

        # reserve plane scope + bus + register exclusions + future update
        self.addr.reserve_planescope(op, start, end)
        self.addr.bus_reserve(start, self.addr.bus_segments_for_op(op))
        self.excl.register(op, start)
        self.addr.register_future(op, start, end)
        # latch: if READ, plan lock from READ.end_us until DOUT ends
        if op.kind == OpKind.READ:
            self.latch.plan_lock_after_read(op.targets, end)

        # assign deterministic op uid (per scheduled op)
        if "uid" not in op.meta:
            op.meta["uid"] = self.stat_scheduled + 1
        if self.logger is not None:
            self.logger.log_op(op, start, end, label_for_read=self._label_for_read(op))

        # obligation assignment stats
        if "obligation" in op.meta:
            self.obl.mark_assigned(op.meta["obligation"])

        # push events
        self._push(start, "OP_START", op); self._push(end, "OP_END", op)

        # hooks
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
                # 의무 선택의 타당성 판단을 위해 now를 고려
                earliest_start = max(self.now, self.addr.available_at(hook.die, hook.plane))
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
                # DOUT 종료 시 래치 해제
                if op.kind == OpKind.DOUT:
                    self.latch.release_on_dout_end(op.targets, self.now)
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
    _seed_rng_from_cfg(CFG)
    _coerce_states_to_fixed(CFG)
    _validate_phase_conditional_cfg(CFG)
    logger = TimelineLogger()
    rejlog = RejectionLogger()
    addr = AddressManager(CFG); excl = ExclusionManager(CFG)
    obl  = ObligationManager(CFG["obligations"])
    latch = LatchManager()
    spe  = PolicyEngine(CFG, addr, obl, excl, rejlog=rejlog, latch=latch)
    sch  = Scheduler(CFG, addr, spe, obl, excl, logger=logger, latch=latch)

    # 2) 실행
    sch.run_until(CFG["policy"]["run_until_us"])

    # 3) DataFrame
    df = logger.to_dataframe()
    df.to_csv("nand_timeline.csv", index=False)

    # 4) 규칙 자동검증
    report = validate_timeline(df, CFG)
    print_validation_report(report, max_rows=30)
    viol_df = violations_to_dataframe(report)
    viol_df.to_csv("nand_violations.csv", index=False)

    # 4.5) Rejection log
    try:
        rejlog.to_csv("reject_log.csv")
        print("\n=== Rejection Summary (by stage/reason) ===")
        for stage, d in rejlog.stats.items():
            total = sum(d.values())
            acc   = rejlog.stage_accepts.get(stage, 0)
            att   = rejlog.stage_attempts.get(stage, 0)
            print(f"- {stage:18s}: attempts={att} accepts={acc} rejects={total}")
            for reason, cnt in sorted(d.items(), key=lambda x: -x[1])[:8]:
                print(f"    {reason:14s}: {cnt}")
    except Exception as e:
        print(f"[REJLOG] skipped: {e}")

    # 5) 시각화
    plot_gantt_by_die(df)  # 모든 die별로 개별 그림
    plot_block_page_sequence_3d_by_die(df, kinds=("ERASE","PROGRAM","READ"),
                                       z_mode="global_die", draw_lines=True)

if __name__=="__main__":
    main()