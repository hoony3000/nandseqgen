# nandsim_p3_constraints.py
# - SR op added (any phase)
# - Phase-conditional distributions updated for CORE_BUSY rules
# - DOUT: global freeze (no ops allowed) via runtime exclusions
# - ExclusionManager: config-driven runtime blocking windows
# - Alias-based READ labels in hooks: SIN_READ / MUL_READ
# - Multi-plane planner + DIE_WIDE CORE_BUSY + Bus gating + Quantize

from __future__ import annotations
import heapq, random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple, Set

SIM_RES_US = 0.01
def quantize(t: float) -> float:
    return round(t / SIM_RES_US) * SIM_RES_US

# ---------------------------- Config ----------------------------
CFG = {
    "rng_seed": 12345,
    "policy": {
        "queue_refill_period_us": 3.0,
        "run_until_us": 180.0,
        "planner_max_tries": 8,
    },
    # phase-conditional: 훅(OP.STATE(.POS)) → 다음 op 분포
    #  - CORE_BUSY 제약 반영
    "phase_conditional": {
        # 기본 분포(일부 예시)
        "READ.ISSUE":                {"MUL_PROGRAM": 0.15, "SIN_PROGRAM": 0.10, "SIN_READ": 0.35, "MUL_READ": 0.25, "SIN_ERASE": 0.15, "SR": 0.00},
        "READ.CORE_BUSY.START":      {"MUL_READ": 0.50, "SIN_READ": 0.20, "SIN_PROGRAM": 0.10, "SIN_ERASE": 0.10, "SR": 0.10},
        "READ.DATA_OUT.START":       {"SIN_READ": 0.50, "MUL_READ": 0.20, "SIN_PROGRAM": 0.15, "SIN_ERASE": 0.10, "SR": 0.05},
        "PROGRAM.ISSUE":             {"SIN_READ": 0.50, "MUL_READ": 0.20, "SIN_PROGRAM": 0.15, "SIN_ERASE": 0.10, "SR": 0.05},
        "PROGRAM.CORE_BUSY.END":     {"SIN_READ": 0.55, "MUL_READ": 0.25, "SIN_PROGRAM": 0.10, "SIN_ERASE": 0.05, "SR": 0.05},
        "ERASE.ISSUE":               {"SIN_PROGRAM": 0.40, "MUL_PROGRAM": 0.20, "SIN_READ": 0.25, "MUL_READ": 0.10, "SR": 0.05},
        "ERASE.CORE_BUSY.END":       {"SIN_READ": 0.50, "MUL_READ": 0.20, "SIN_PROGRAM": 0.10, "MUL_PROGRAM": 0.05, "SR": 0.15},

        # === 규칙 반영: CORE_BUSY 중 허용/금지 ===
        # PROGRAM/ERASE 의 CORE_BUSY 중에는 READ/PROGRAM/ERASE 모두 금지 → SR만 허용
        "PROGRAM.CORE_BUSY":         {"SR": 1.0},
        "ERASE.CORE_BUSY":           {"SR": 1.0},
        # MUL_READ 의 CORE_BUSY 중에는 READ/PROGRAM/ERASE 모두 금지 → SR만 허용
        "MUL_READ.CORE_BUSY":        {"SR": 1.0},
        # SIN_READ 의 CORE_BUSY 중에는 SIN_READ/SR만 허용, MUL_READ/PROGRAM/ERASE 금지
        "SIN_READ.CORE_BUSY":        {"SIN_READ": 0.6, "SR": 0.4},

        "DEFAULT":                   {"SIN_READ": 0.55, "SIN_PROGRAM": 0.25, "SIN_ERASE": 0.10, "SR": 0.10},
    },

    "weights": {  # 백오프용
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

    # 런타임 배타 규칙(스케줄 시 윈도우 등록 → 제안 시 허용 검사)
    "constraints": {
        "exclusions": [
            # PROGRAM CORE_BUSY 동안: READ/PROGRAM/ERASE 금지 (die 스코프)
            {"when": {"op": "PROGRAM", "states": ["CORE_BUSY"]}, "scope": "DIE",
             "blocks": ["BASE:READ", "BASE:PROGRAM", "BASE:ERASE"]},
            # ERASE CORE_BUSY 동안: READ/PROGRAM/ERASE 금지 (die 스코프)
            {"when": {"op": "ERASE", "states": ["CORE_BUSY"]}, "scope": "DIE",
             "blocks": ["BASE:READ", "BASE:PROGRAM", "BASE:ERASE"]},
            # MUL_READ CORE_BUSY 동안: READ/PROGRAM/ERASE 금지 (die 스코프)
            {"when": {"op": "READ", "alias": "MUL", "states": ["CORE_BUSY"]}, "scope": "DIE",
             "blocks": ["BASE:READ", "BASE:PROGRAM", "BASE:ERASE"]},
            # SIN_READ CORE_BUSY 동안: MUL_READ/PROGRAM/ERASE 금지(READ 단일은 허용)
            {"when": {"op": "READ", "alias": "SIN", "states": ["CORE_BUSY"]}, "scope": "DIE",
             "blocks": ["ALIAS:MUL_READ", "BASE:PROGRAM", "BASE:ERASE"]},
            # DOUT 전체 구간 동안: 전역 ALL 금지
            {"when": {"op": "DOUT", "states": ["*"]}, "scope": "GLOBAL",
             "blocks": ["ANY"]},
        ]
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
    meta: Dict[str,Any] = field(default_factory=dict)

# ---------------------------- Utils ----------------------------
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

# ---------------------------- Builders ----------------------------
def build_operation(kind: OpKind, cfg_op: Dict[str, Any], targets: List[Address]) -> Operation:
    states=[]
    for s in cfg_op["states"]:
        states.append(StateSeg(name=s["name"], dur_us=sample_dist(s["dist"]), bus=bool(s.get("bus", False))))
    return Operation(kind=kind, targets=targets, states=states)

def get_op_duration(op: Operation) -> float:
    return sum(seg.dur_us for seg in op.states)

# ---------------------------- Address & Bus Managers ----------------------------
class AddressManager:
    def __init__(self, cfg: Dict[str, Any]):
        topo=cfg["topology"]; self.cfg=cfg
        self.dies=topo["dies"]; self.planes=topo["planes_per_die"]
        self.pages_per_block=topo["pages_per_block"]; self.blocks_per_plane=topo["blocks_per_plane"]
        self.available={(0,p):0.0 for p in range(self.planes)}
        self.cursors={(0,p):[0,0,0] for p in range(self.planes)}  # block, next_pgm, next_rd
        self.programmed={(0,p):set() for p in range(self.planes)}  # (block,page)
        self.resv={(0,p):[] for p in range(self.planes)}           # (start,end,block)
        self.bus_resv: List[Tuple[float,float]] = []

    def available_at(self, die:int, plane:int)->float: return self.available[(die,plane)]

    def earliest_start_for_scope(self, die:int, scope: Scope, plane_set: Optional[List[int]]=None)->float:
        if scope==Scope.DIE_WIDE: planes=list(range(self.planes))
        elif scope==Scope.PLANE_SET and plane_set is not None: planes=plane_set
        else: planes=[plane_set[0]] if plane_set else [0]
        return max(self.available[(die,p)] for p in planes)

    def observe_states(self, die:int, plane:int, now_us: float):
        prog=len(self.programmed[(die,plane)])
        pgmable_ratio="mid" if prog<10 else "low"
        readable_ratio="mid" if prog>0 else "low"
        plane_busy_frac="high" if self.available_at(die,plane)>now_us else "low"
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls":"host"},
                {"plane_busy_frac": plane_busy_frac})

    # bus segments (relative offsets)
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

    # planner (non-greedy)
    def _random_plane_sets(self, fanout:int, tries:int, start_plane:int)->List[List[int]]:
        P=list(range(self.planes)); out=[]; 
        for _ in range(tries):
            cand=set(random.sample(P, min(fanout,len(P))))
            if start_plane not in cand and random.random()<0.6:
                if len(cand)>0: cand.pop()
                cand.add(start_plane)
            if len(cand)==fanout: out.append(sorted(cand))
        uniq=[]; seen=set()
        for ps in out:
            k=tuple(ps)
            if k not in seen: uniq.append(ps); seen.add(k)
        return uniq

    def _pages_present(self, die:int, plane:int)->Set[int]:
        return {p for (_,p) in self.programmed[(die,plane)]}

    def plan_multipane(self, kind: OpKind, die:int, start_plane:int, desired_fanout:int, interleave:bool)\
            -> Optional[Tuple[List[Address], List[int], Scope]]:
        if desired_fanout<1: desired_fanout=1
        tries=self.cfg["policy"]["planner_max_tries"]
        for f in range(desired_fanout, 0, -1):
            for plane_set in self._random_plane_sets(f, tries, start_plane):
                if kind==OpKind.READ:
                    commons=None
                    for pl in plane_set:
                        pages=self._pages_present(die,pl)
                        commons=pages if commons is None else (commons & pages)
                        if not commons: break
                    if not commons: continue
                    page=random.choice(sorted(list(commons)))
                    targets=[]
                    for pl in plane_set:
                        blks=[b for (b,p) in self.programmed[(die,pl)] if p==page]
                        blk=blks[0] if blks else 0
                        targets.append(Address(die,pl,blk,page))
                    scope=Scope.PLANE_SET

                elif kind==OpKind.PROGRAM:
                    nxt=[]
                    for pl in plane_set:
                        b, pgm_p, _ = self.cursors[(die,pl)]
                        nxt.append(pgm_p)
                    if len(set(nxt))!=1: continue
                    page=nxt[0]; targets=[]
                    for pl in plane_set:
                        b, pgm_p, _ = self.cursors[(die,pl)]
                        targets.append(Address(die,pl,b,page))
                    scope=Scope.DIE_WIDE

                elif kind==OpKind.ERASE:
                    targets=[]
                    for pl in plane_set:
                        b=self.cursors[(die,pl)][0]
                        targets.append(Address(die,pl,b,None))
                    scope=Scope.DIE_WIDE

                elif kind==OpKind.SR:
                    targets=[Address(die,start_plane,0,None)]
                    scope=Scope.NONE

                else:
                    continue
                return targets, plane_set, scope
        return None

    def precheck_planescope(self, kind: OpKind, targets: List[Address], start_hint: float, scope: Scope)->bool:
        start_hint=quantize(start_hint); end_hint=start_hint
        die=targets[0].die
        if scope==Scope.DIE_WIDE: planes={(die,p) for p in range(self.planes)}
        elif scope==Scope.PLANE_SET: planes={(t.die,t.plane) for t in targets}
        else: planes={(targets[0].die, targets[0].plane)}
        for (d,p) in planes:
            for (s,e,_) in self.resv[(d,p)]:
                if not (end_hint<=s or e<=start_hint): return False
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

    def commit(self, op: Operation):
        for t in op.targets:
            key=(t.die,t.plane)
            if op.kind==OpKind.PROGRAM and t.page is not None:
                self.programmed[key].add((t.block,t.page))
                b, pgm_p, rd = self.cursors[(t.die,t.plane)]
                pgm_p+=1
                if pgm_p>=self.pages_per_block:
                    pgm_p=0; b=(b+1)%self.blocks_per_plane
                self.cursors[(t.die,t.plane)]=[b,pgm_p,rd]
            elif op.kind==OpKind.ERASE:
                self.programmed[key]={pp for pp in self.programmed[key] if pp[0]!=t.block}

# ---------------------------- Exclusion Manager (runtime blocking) ----------------------------
@dataclass
class ExclWindow:
    start: float; end: float
    scope: str              # "GLOBAL" or "DIE"
    die: Optional[int]
    tokens: Set[str]        # e.g., {"ANY"}, {"BASE:READ"}, {"ALIAS:MUL_READ"}

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
        # check global
        for w in self.global_windows:
            if not (end<=w.start or w.end<=start):
                if any(self._token_blocks(tok, op) for tok in w.tokens):
                    return False
        # check die
        die=op.targets[0].die
        for w in self.die_windows.get(die, []):
            if not (end<=w.start or w.end<=start):
                if any(self._token_blocks(tok, op) for tok in w.tokens):
                    return False
        return True

    def register(self, op: Operation, start: float):
        rules = self.cfg.get("constraints",{}).get("exclusions",[])
        die = op.targets[0].die
        for r in rules:
            when=r["when"]; if_op=when.get("op")
            if if_op != op.kind.name: 
                # alias match for READ(SIN/MUL)
                if not (op.kind==OpKind.READ and if_op=="READ"): 
                    continue
            alias_need = when.get("alias")
            if alias_need=="MUL" and not (op.meta.get("arity",1)>1): 
                continue
            if alias_need=="SIN" and not (op.meta.get("arity",1)==1): 
                continue
            states = when.get("states", ["*"])
            windows = self._state_windows(op, start, states)
            scope=r.get("scope","GLOBAL"); tokens=set(r.get("blocks",[]))
            for (s0,s1) in windows:
                w=ExclWindow(start=quantize(s0), end=quantize(s1), scope=scope, die=(die if scope=="DIE" else None), tokens=tokens)
                if scope=="GLOBAL":
                    self.global_windows.append(w)
                else:
                    self.die_windows.setdefault(die,[]).append(w)

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
        self.heap: List[_ObHeapItem] = []; self._seq=0

    def on_commit(self, op: Operation, now_us: float):
        for spec in self.specs:
            if spec["issuer"] == op.kind.name:
                dt=sample_dist(spec["window_us"])
                ob=Obligation(require=OpKind[spec["require"]], targets=op.targets,
                              deadline_us=quantize(now_us+dt),
                              boost_factor=spec["priority_boost"]["boost_factor"],
                              hard_slot=spec["priority_boost"].get("hard_slot", False))
                heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._seq, ob=ob))
                self._seq+=1
                first=op.targets[0]
                print(f"[{now_us:7.2f} us] OBLIG  created: {op.kind.name} -> {ob.require.name} by {ob.deadline_us:7.2f} us, target(d{first.die},p{first.plane})")

    def pop_urgent(self, now_us: float, die:int, plane:int, horizon_us: float, earliest_start: float) -> Optional[Obligation]:
        if not self.heap: return None
        kept=[]; chosen=None
        now_us=quantize(now_us); earliest_start=quantize(earliest_start)
        while self.heap and not chosen:
            item=heapq.heappop(self.heap); ob=item.ob
            plane_list={a.plane for a in ob.targets}
            same_die=(ob.targets[0].die==die); same_plane=(plane in plane_list)
            in_horizon=((ob.deadline_us-now_us)<=max(horizon_us,0.0)) or ob.hard_slot
            feasible=(earliest_start<=ob.deadline_us)
            if same_die and same_plane and in_horizon and feasible: chosen=ob; break
            kept.append(item)
        for it in kept: heapq.heappush(self.heap, it)
        return chosen

# ---------------------------- Policy Engine ----------------------------
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
        # scope-aware earliest start (already computed outside; but ensure not earlier than plane availability)
        start = self.addr.earliest_start_for_scope(die, scope, plane_set)
        dur = get_op_duration(op); end=quantize(start+dur)
        return self.excl.allowed(op, start, end)

    def propose(self, now_us: float, hook: PhaseHook, g: Dict[str,str], l: Dict[str,str], earliest_start: float) -> Optional[Operation]:
        die, hook_plane = hook.die, hook.plane

        # 0) obligation 우선
        ob=self.obl.pop_urgent(now_us, die, hook_plane, horizon_us=10.0, earliest_start=earliest_start)
        if ob:
            cfg_op=self.cfg["op_specs"][ob.require.name]; op=build_operation(ob.require, cfg_op, ob.targets)
            op.meta["scope"]=cfg_op["scope"]; op.meta["plane_list"]=sorted({a.plane for a in ob.targets}); op.meta["arity"]=len(op.meta["plane_list"])
            # gating check: plane, bus, exclusion
            scope=Scope[op.meta["scope"]]; plane_set=op.meta["plane_list"]
            start_hint=self.addr.earliest_start_for_scope(die, scope, plane_set)
            if self.addr.precheck_planescope(op.kind, op.targets, start_hint, scope)\
               and self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op))\
               and self._exclusion_ok(op, die, plane_set, start_hint):
                op.meta["source"]="obligation"; op.meta["phase_key_used"]="(obligation)"; return op

        # 1) phase-conditional (alias 키 허용 + SR 포함)
        allow=set(list(OP_ALIAS.keys())+["READ","PROGRAM","ERASE","SR"])
        dist, used_key = get_phase_dist(self.cfg, hook.label)
        if dist:
            pick=roulette_pick(dist, allow)
            if pick:
                base, alias_const = resolve_alias(pick)
                kind=OpKind[base]
                # SR은 단일 플레인
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
                    if self.addr.precheck_planescope(kind, targets, start_hint, scope)\
                       and self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op))\
                       and self._exclusion_ok(op, die, plane_set, start_hint):
                        op.meta["source"]="policy.phase_conditional"; op.meta["phase_key_used"]=used_key; return op
            # 실패 시 백오프

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
        if not (self.addr.precheck_planescope(kind, targets, start_hint, scope)\
                and self.addr.bus_precheck(start_hint, self.addr.bus_segments_for_op(op))\
                and self._exclusion_ok(op, die, plane_set, start_hint)):
            return None
        op.meta["source"]="policy.score_backoff"; op.meta["phase_key_used"]="(score_backoff)"; return op

# ---------------------------- Scheduler ----------------------------
def _addr_str(a: Address)->str: return f"(d{a.die},p{a.plane},b{a.block},pg{a.page})"

class Scheduler:
    def __init__(self, cfg, addr: AddressManager, spe: PolicyEngine, obl: ObligationManager, excl: ExclusionManager):
        self.cfg=cfg; self.addr=addr; self.SPE=spe; self.obl=obl; self.excl=excl
        self.now=0.0; self.ev=[]; self._seq=0
        self.stat_propose_calls=0; self.stat_scheduled=0
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
        # plane scope reserve + bus reserve + exclusion windows register
        self.addr.reserve_planescope(op, start, end)
        self.addr.bus_reserve(start, self.addr.bus_segments_for_op(op))
        self.excl.register(op, start)

        # events
        self._push(start, "OP_START", op); self._push(end, "OP_END", op)

        # phase hooks (라벨: READ는 SIN_READ/MUL_READ)
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
                self.addr.commit(op); self.obl.on_commit(op, self.now)

        print(f"\n=== Stats ===")
        print(f"propose calls : {self.stat_propose_calls}")
        print(f"scheduled ops : {self.stat_scheduled}")
        if self.stat_propose_calls:
            print(f"accept ratio  : {100.0*self.stat_scheduled/self.stat_propose_calls:.1f}%")
        print(f"(note) runtime exclusions: GLOBAL={len(self.excl.global_windows)}, DIE={sum(len(v) for v in self.excl.die_windows.values())}")

# ---------------------------- Selection overrides ----------------------------
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

# ---------------------------- Main ----------------------------
def main():
    random.seed(CFG["rng_seed"])
    addr=AddressManager(CFG); excl=ExclusionManager(CFG)
    obl=ObligationManager(CFG["obligations"])
    spe=PolicyEngine(CFG, addr, obl, excl)
    sch=Scheduler(CFG, addr, spe, obl, excl)
    print("=== NAND Sequence Generator (P3: SR + CORE_BUSY constraints + DOUT freeze) ===")
    sch.run_until(CFG["policy"]["run_until_us"])
    print("=== Done ===")

if __name__=="__main__":
    main()
