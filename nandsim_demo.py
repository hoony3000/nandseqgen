# nandsim_demo_p6_latchguard_fixparen.py
# - LatchGuard: Protect (die,plane) from PROGRAM/ERASE between READ.CORE_BUSY.END and DOUT.END
# - precheck blocks PROGRAM/ERASE if they overlap any active guard interval
# - PolicyEngine guard: avoid proposing ops that would collide with LatchGuard
# - READ.CORE_BUSY.END hook added; optional fast DOUT obligation at that hook
# - Obligation fixes: requeue, hard-slot relax, obligation-deadline guard
# - FIX: pop_urgent() next-deadline horizon check parenthesis

from __future__ import annotations
import heapq, random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple, Set
from collections import defaultdict

SIM_RES_US = 0.01
def quantize(t: float) -> float:
    return round(t / SIM_RES_US) * SIM_RES_US

CFG = {
    "rng_seed": 12345,
    "policy": {"queue_refill_period_us": 50.0, "lookahead_k": 4, "run_until_us": 220.0},
    "seq_read": {
        "window": 4, "deadline_spacing_us": 2.0, "softlock_grace_us": 8.0, "boost_window_us": 2.5,
    },
    "latch_guard": {
        "scope": "plane",
        "margin_us": 2.0,
        "merge_gap_us": 0.05,
        "policy_guard": True,
        "fast_dout": True
    },
    "weights": {
        "base": {"host": {"READ": 0.85, "PROGRAM": 0.10, "ERASE": 0.05, "SR": 0.05, "RESET": 0.00, "DOUT": 0.00}},
        "g_state": {"pgmable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.8},
                    "readable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.9}},
        "g_local": {"plane_busy_frac": {"low": 1.2, "mid": 1.0, "high": 0.9}},
        "g_phase": {
            "READ": {"START_NEAR": 1.2, "MID_NEAR": 0.9, "END_NEAR": 1.2},
            "PROGRAM": {"START_NEAR": 1.1, "MID_NEAR": 1.0, "END_NEAR": 0.9},
            "ERASE": {"START_NEAR": 0.9, "MID_NEAR": 1.1, "END_NEAR": 1.1},
            "SR": {"START_NEAR": 1.0, "MID_NEAR": 1.0, "END_NEAR": 1.0},
            "RESET": {"START_NEAR": 1.0, "MID_NEAR": 1.0, "END_NEAR": 1.0},
        },
    },
    # Phase-conditional policy rules
    "phase_conditional": {
        "READ.ISSUE":      {}, "PROGRAM.ISSUE":   {}, "ERASE.ISSUE": {}, "SR.ISSUE": {}, "DOUT.ISSUE": {},
        "ERASE.CORE_BUSY":   {"SR": 1.0},
        "PROGRAM.CORE_BUSY": {"SR": 1.0},
        "READ.CORE_BUSY":    {"READ": 1.0, "SR": 0.5},
        "DEFAULT":           {"READ": 0.60, "PROGRAM": 0.30, "ERASE": 0.10, "SR": 0.10},
    },
    "selection": {
        "defaults": {"READ": {"fanout":1,"interleave":True}, "PROGRAM":{"fanout":1,"interleave":True},
                     "ERASE":{"fanout":1,"interleave":False}, "SR":{"fanout":1,"interleave":True}},
        "phase_overrides": {"READ.CORE_BUSY.START": {"fanout": 2, "interleave": True}}
    },
    "op_specs": {
        "READ": {
            "states": [
                {"name": "ISSUE",     "dist": {"kind": "fixed",  "value": 0.4}},
                {"name": "CORE_BUSY", "dist": {"kind": "normal", "mean": 8.0, "std": 1.5, "min": 2.0}},
                {"name": "DATA_OUT",  "dist": {"kind": "normal", "mean": 2.0, "std": 0.4, "min": 0.5}},
            ],
            "hooks": [
                {"when": "STATE_START", "states": ["ISSUE", "CORE_BUSY", "DATA_OUT"], "jitter_us": 0.1},
                {"when": "STATE_MID",   "states": ["CORE_BUSY"],                      "jitter_us": 0.2},
                {"when": "STATE_END",   "states": ["CORE_BUSY", "DATA_OUT"],          "jitter_us": 0.05},
            ],
        },
        "DOUT": {
            "states": [
                {"name": "ISSUE",     "dist": {"kind": "fixed",  "value": 0.2}},
                {"name": "DATA_OUT",  "dist": {"kind": "normal", "mean": 1.0, "std": 0.2, "min": 0.2}},
            ],
            "hooks": [],
        },
        "PROGRAM": {
            "states": [
                {"name": "ISSUE",     "dist": {"kind": "fixed",  "value": 0.4}},
                {"name": "CORE_BUSY", "dist": {"kind": "normal", "mean": 20.0, "std": 3.0, "min": 8.0}},
            ],
            "hooks": [{"when": "STATE_END", "states": ["CORE_BUSY"], "jitter_us": 0.2}],
        },
        "ERASE": {
            "states": [
                {"name": "ISSUE",     "dist": {"kind": "fixed",  "value": 0.4}},
                {"name": "CORE_BUSY", "dist": {"kind": "normal", "mean": 40.0, "std": 5.0, "min": 15.0}},
            ],
            "hooks": [{"when": "STATE_END", "states": ["CORE_BUSY"], "jitter_us": 0.2}],
        },
        "SR": {
            "states": [
                {"name": "ISSUE",     "dist": {"kind": "fixed",  "value": 0.1}},
                {"name": "CORE_BUSY", "dist": {"kind": "fixed",  "value": 0.4}},
            ],
            "hooks": [],
        },
    },
    "obligations": [
        {
            "issuer": "READ",
            "require": "DOUT",
            "window_us": {"kind": "normal", "mean": 3.0, "std": 0.8, "min": 0.6},
            "priority_boost": {"start_us_before_deadline": 2.5, "boost_factor": 2.0, "hard_slot": True},
        }
    ],
    "priority": {"order": ["HOST","READ","DOUT","ERASE","PROGRAM_NEW","PROGRAM_TARGET","SR","RESET"],
                 "starvation_aging_alpha": 0.01},
    "topology": {"dies": 1, "planes_per_die": 4, "blocks_per_plane": 8, "pages_per_block": 16},
}

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
    block: Optional[int] = None
    page: Optional[int]  = None

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

def sample_dist(d: Dict[str, Any]) -> float:
    k = d["kind"]
    if k == "fixed": return float(d["value"])
    if k == "normal":
        v = random.gauss(d["mean"], d["std"])
        return max(v, d.get("min", 0.0))
    if k == "exp": return random.expovariate(d["lambda"])
    raise ValueError(f"unknown dist kind: {k}")

def expected_value(d: Dict[str, Any]) -> float:
    k = d["kind"]
    if k == "fixed": return float(d["value"])
    if k == "normal": return float(d["mean"])
    if k == "exp": return 1.0/float(d["lambda"])
    return 0.0

def estimate_mean_duration(op_name: str, cfg: Dict[str,Any]) -> float:
    spec = cfg["op_specs"][op_name]["states"]
    return sum(expected_value(s["dist"]) for s in spec)

def rand_jitter(ampl_us: float) -> float:
    return 0.0 if ampl_us<=0 else random.uniform(-ampl_us, ampl_us)

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
        if dist is not None:
            return dist, key
    return None, None

def get_phase_selection_override(cfg: Dict[str,Any], hook_label: str, kind_name: str):
    op, state, pos = parse_hook_key(hook_label)
    po = cfg.get("selection", {}).get("phase_overrides", {})
    keys = []
    if op and state and pos: keys.append(f"{op}.{state}.{pos}")
    if op and state:         keys.append(f"{op}.{state}")
    for k in keys:
        val = po.get(k)
        if val:
            return int(val.get("fanout", 1)), bool(val.get("interleave", True))
    dflt = cfg.get("selection", {}).get("defaults", {}).get(kind_name, {"fanout":1,"interleave":True})
    return int(dflt.get("fanout",1)), bool(dflt.get("interleave", True))

def get_op_duration(op: Operation) -> float:
    return sum(seg.dur_us for seg in op.states)

def get_state_end_rel(op: Operation, state_name: str) -> Optional[float]:
    acc = 0.0
    for seg in op.states:
        acc += seg.dur_us
        if seg.name == state_name:
            return acc
    return None

def build_operation(kind: OpKind, cfg_op: Dict[str, Any], targets: List[Address]) -> Operation:
    states=[]
    for s in cfg_op["states"]:
        states.append(StateSeg(name=s["name"], dur_us=sample_dist(s["dist"])))
    return Operation(kind=kind, targets=targets, states=states)

def make_phase_hooks(cfg: Dict[str,Any], op: Operation, start_us: float, die:int, plane:int, block:Optional[int], page:Optional[int]) -> List[PhaseHook]:
    cfg_op = cfg["op_specs"][op.kind.name]
    hooks_cfg = cfg_op.get("hooks", [])
    hooks=[]
    cur = start_us
    for seg in op.states:
        seg_start = cur
        seg_mid   = cur + seg.dur_us*0.5
        seg_end   = cur + seg.dur_us
        for rule in hooks_cfg:
            if seg.name not in rule["states"]: continue
            jitter = rule.get("jitter_us", 0.0)
            if rule["when"]=="STATE_START":
                hooks.append(PhaseHook(time_us=quantize(seg_start + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.START", die=die, plane=plane, block=block, page=page))
            elif rule["when"]=="STATE_MID":
                hooks.append(PhaseHook(time_us=quantize(seg_mid   + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.MID",   die=die, plane=plane, block=block, page=page))
            elif rule["when"]=="STATE_END":
                hooks.append(PhaseHook(time_us=quantize(seg_end   + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.END",   die=die, plane=plane, block=block, page=page))
        cur = seg_end
    return hooks

# ---------------- Address Manager + LatchGuard ----------------

class AddressManager:
    def __init__(self, cfg: Dict[str, Any]):
        topo = cfg["topology"]
        self.cfg = cfg
        self.dies = topo["dies"]
        self.planes = topo["planes_per_die"]
        self.pages_per_block = topo["pages_per_block"]
        self.blocks_per_plane = topo["blocks_per_plane"]

        self.available: Dict[Tuple[int,int], float] = {(0,p): 0.0 for p in range(self.planes)}
        self.cursors: Dict[Tuple[int,int], List[int]] = {(0,p): [0,0,0] for p in range(self.planes)}
        self.programmed: Dict[Tuple[int,int], Set[Tuple[int,int]]] = {(0,p): set() for p in range(self.planes)}
        self.resv: Dict[Tuple[int,int], List[Tuple[float,float,Optional[int]]]] = {(0,p): [] for p in range(self.planes)}

        self.seq_locks: Dict[Tuple[int,int,int], Dict[str,Any]] = {}
        self.emgc: Dict[Tuple[int,int], bool] = {(0,p): False for p in range(self.planes)}

        # LatchGuard: (die,plane) -> intervals
        self.latch_guard: Dict[Tuple[int,int], List[Dict[str,Any]]] = {(0,p): [] for p in range(self.planes)}

    def set_emergency_gc(self, die:int, plane:int, active: bool):
        self.emgc[(die,plane)] = bool(active)

    def set_seq_lock(self, die:int, plane:int, block:int, expiry_us: float, allowed_pages: Set[int]):
        self.seq_locks[(die,plane,block)] = {"expiry": quantize(expiry_us), "allowed_pages": set(allowed_pages)}

    def update_seq_lock_pages(self, die:int, plane:int, block:int, allowed_pages: Set[int], extend_expiry: Optional[float]=None):
        key=(die,plane,block); lk = self.seq_locks.get(key)
        if not lk: return
        lk["allowed_pages"] = set(allowed_pages)
        if extend_expiry is not None: lk["expiry"] = quantize(max(lk["expiry"], extend_expiry))

    def clear_seq_lock(self, die:int, plane:int, block:int):
        self.seq_locks.pop((die,plane,block), None)

    def available_at(self, die:int, plane:int) -> float:
        return self.available[(die,plane)]

    def observe_states(self, die:int, plane:int, now_us: float):
        prog = len(self.programmed[(die,plane)])
        pgmable_ratio  = "mid" if prog < 10 else "low"
        readable_ratio = "mid" if prog > 0 else "low"
        plane_busy_frac = "high" if self.available_at(die,plane) > now_us else "low"
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls": "host"},
                {"plane_busy_frac": plane_busy_frac})

    def _plane_ring(self, start_plane:int, fanout:int, interleave:bool) -> List[int]:
        if fanout <= 1: return [start_plane]
        ring = []; P = self.planes; idx=start_plane
        for _ in range(fanout): ring.append(idx); idx=(idx+1)%P
        return ring

    def select(self, kind: OpKind, die:int, plane:int, fanout:int=1, interleave:bool=True) -> List[Address]:
        planes = self._plane_ring(plane, fanout, interleave)
        targets: List[Address] = []
        for pl in planes:
            if kind == OpKind.READ:
                locks_here = [k for k in self.seq_locks.keys() if k[0]==die and k[1]==pl]
                page=None; block=0
                if locks_here:
                    d,p,b = locks_here[0]; lk = self.seq_locks[(d,p,b)]
                    if lk["allowed_pages"]:
                        page = sorted(lk["allowed_pages"])[0]; block=b
                if page is None:
                    prog = sorted(self.programmed[(die,pl)])
                    block, page = (prog[0] if prog else (0,0))
                targets.append(Address(die, pl, block=block, page=page))
            elif kind == OpKind.DOUT:
                raise RuntimeError("DOUT selection must be provided by obligation targets")
            elif kind == OpKind.PROGRAM:
                b, pgm_p, _ = self.cursors[(die,pl)]
                targets.append(Address(die, pl, block=b, page=pgm_p))
                pgm_p += 1
                if pgm_p >= self.pages_per_block: pgm_p=0; b=(b+1)%self.blocks_per_plane
                self.cursors[(die,pl)] = [b, pgm_p, self.cursors[(die,pl)][2]]
            elif kind == OpKind.ERASE:
                b = self.cursors[(die,pl)][0]
                targets.append(Address(die, pl, block=b, page=None))
            elif kind == OpKind.SR:
                targets.append(Address(die, pl, block=0, page=None))
            else:
                targets.append(Address(die, pl, block=0, page=0))
        return targets

    def _merge_intervals(self, ivs: List[Dict[str,Any]]):
        if not ivs: return []
        eps = self.cfg["latch_guard"]["merge_gap_us"]
        ivs.sort(key=lambda x: (x["start"], x["end"]))
        out=[ivs[0]]
        for it in ivs[1:]:
            last=out[-1]
            if it["start"] <= last["end"] + eps:
                last["end"] = max(last["end"], it["end"])
            else:
                out.append(it)
        return out

    def add_guard(self, die:int, plane:int, start_us: float, end_us: float, scope:str="plane",
                  block: Optional[int]=None, page: Optional[int]=None, reason:str="READ->DOUT"):
        key=(die,plane)
        item={"start":quantize(start_us), "end":quantize(end_us), "scope":scope,
              "block":block, "page":page, "reason":reason}
        self.latch_guard[key].append(item)
        self.latch_guard[key] = self._merge_intervals(self.latch_guard[key])

    def update_guard_end(self, die:int, plane:int, page: Optional[int], block: Optional[int], new_end_us: float):
        key=(die,plane)
        new_end = quantize(new_end_us)
        for iv in self.latch_guard[key]:
            if iv["scope"]=="plane":
                iv["end"] = max(iv["end"], new_end)
        self.latch_guard[key] = self._merge_intervals(self.latch_guard[key])

    def clear_guard(self, die:int, plane:int, page: Optional[int], block: Optional[int]):
        # placeholder (optional tighter cleanup can be added)
        pass

    def query_guard(self, die:int, plane:int, start_us: float, end_us: float, block: Optional[int]=None) -> bool:
        key=(die,plane)
        start=quantize(start_us); end=quantize(end_us)
        for iv in self.latch_guard[key]:
            if not (end <= iv["start"] or iv["end"] <= start):
                if iv["scope"]=="plane" or (block is not None and iv.get("block")==block):
                    return True
        return False

    def precheck(self, kind: OpKind, targets: List[Address], start_hint: float) -> bool:
        start_hint = quantize(start_hint); end_hint = start_hint
        seen_planes=set()
        for t in targets:
            keyp=(t.die,t.plane)
            if keyp not in seen_planes:
                seen_planes.add(keyp)
                for (s,e,_) in self.resv[keyp]:
                    if not (end_hint <= s or e <= start_hint): return False
            if t.block is not None:
                keyb=(t.die,t.plane,t.block); lk=self.seq_locks.get(keyb)
                if lk and start_hint < lk["expiry"]:
                    if kind == OpKind.READ:
                        if t.page is None or t.page not in lk["allowed_pages"]: return False
                    if kind in (OpKind.PROGRAM, OpKind.ERASE):
                        if not self.emgc.get((t.die,t.plane), False): return False
            if kind in (OpKind.PROGRAM, OpKind.ERASE):
                if self.query_guard(t.die, t.plane, start_hint, end_hint, block=t.block):
                    return False
        return True

    def reserve(self, die:int, plane:int, start:float, end:float, block: Optional[int]=None):
        start=quantize(start); end=quantize(end)
        self.available[(die,plane)] = max(self.available[(die,plane)], end)
        self.resv[(die,plane)].append((start, end, block))

    def commit(self, op: Operation):
        t = op.targets[0]; key=(t.die,t.plane)
        if op.kind == OpKind.PROGRAM and t.page is not None:
            self.programmed[key].add((t.block, t.page))
        elif op.kind == OpKind.ERASE:
            self.programmed[key] = {pp for pp in self.programmed[key] if pp[0] != t.block}

# ---------------- Obligation Manager ----------------

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
    is_seq: bool = False
    seq_id: Optional[int] = None

@dataclass
class SeqReadContext:
    seq_id: int; die: int; plane: int; block: int
    start_page: int; total_pages: int; next_page: int; window: int
    active_pages: Set[int] = field(default_factory=set); finished: bool = False

class ObligationManager:
    def __init__(self, cfg_list: List[Dict[str,Any]], addr: AddressManager, cfg: Dict[str,Any]):
        self.specs = cfg_list; self.addr = addr; self.cfg = cfg
        self.heap: List[_ObHeapItem] = []; self._seq = 0; self._obseq = 0
        self.seq_ctx_by_id: Dict[int, SeqReadContext] = {}; self.seq_by_block: Dict[Tuple[int,int,int], int] = {}
        self.created_by_kind = defaultdict(int)

    def _push_ob(self, ob: Obligation):
        heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._obseq, ob=ob))
        self._obseq += 1
        self.created_by_kind[ob.require.name] += 1

    def requeue(self, ob: Obligation):
        heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._obseq, ob=ob))
        self._obseq += 1

    def peek_next_deadline_for_plane(self, die:int, plane:int) -> Optional[float]:
        mn = None
        for item in self.heap:
            ob = item.ob
            if ob.targets and ob.targets[0].die==die and ob.targets[0].plane==plane:
                if mn is None or ob.deadline_us < mn:
                    mn = ob.deadline_us
        return mn

    def _make_deadline(self, base: float, idx: int=0, spacing: float=0.0) -> float:
        return quantize(base + idx * spacing)

    def start_seq_read(self, now_us: float, die:int, plane:int, block:int, start_page:int, count:int, window: Optional[int]=None):
        W = window if window is not None else self.cfg["seq_read"]["window"]
        seq_id = self._seq; self._seq += 1
        ctx = SeqReadContext(seq_id=seq_id, die=die, plane=plane, block=block,
                             start_page=start_page, total_pages=count, next_page=start_page, window=W)
        self.seq_ctx_by_id[seq_id] = ctx; self.seq_by_block[(die,plane,block)] = seq_id

        spacing = self.cfg["seq_read"]["deadline_spacing_us"]
        to_seed = min(W, count); allowed: Set[int] = set()
        for i in range(to_seed):
            p = ctx.next_page; ctx.active_pages.add(p); allowed.add(p); ctx.next_page += 1
            dl = self._make_deadline(now_us, i, spacing)
            ob = Obligation(require=OpKind.READ, targets=[Address(die,plane,block,p)],
                            deadline_us=dl, boost_factor=1.0, hard_slot=True, is_seq=True, seq_id=seq_id)
            self._push_ob(ob)
        expiry = self._make_deadline(now_us, to_seed, spacing) + self.cfg["seq_read"]["softlock_grace_us"]
        self.addr.set_seq_lock(die, plane, block, expiry_us=expiry, allowed_pages=allowed)
        print(f"[{now_us:7.2f} us] SEQRD  start: die{die}/pl{plane}/b{block} p{start_page}..{start_page+count-1} W={W}")

    def _refill_after_read(self, now_us: float, seq_id: int, page_done: int):
        ctx = self.seq_ctx_by_id.get(seq_id)
        if not ctx or ctx.finished: return
        if page_done in ctx.active_pages: ctx.active_pages.remove(page_done)
        if ctx.next_page < ctx.start_page + ctx.total_pages:
            while len(ctx.active_pages) < ctx.window and ctx.next_page < ctx.start_page + ctx.total_pages:
                p = ctx.next_page; ctx.active_pages.add(p); ctx.next_page += 1
                dl = quantize(now_us + self.cfg["seq_read"]["deadline_spacing_us"])
                ob = Obligation(require=OpKind.READ, targets=[Address(ctx.die, ctx.plane, ctx.block, p)],
                                deadline_us=dl, boost_factor=1.0, hard_slot=True, is_seq=True, seq_id=seq_id)
                self._push_ob(ob)
        allowed = set(ctx.active_pages); grace = self.cfg["seq_read"]["softlock_grace_us"]
        expiry = quantize(now_us + grace)
        self.addr.update_seq_lock_pages(ctx.die, ctx.plane, ctx.block, allowed_pages=allowed, extend_expiry=expiry)
        if not ctx.active_pages and ctx.next_page >= ctx.start_page + ctx.total_pages:
            ctx.finished = True
            print(f"[{now_us:7.2f} us] SEQRD  finished READ: die{ctx.die}/pl{ctx.plane}/b{ctx.block}")

    def on_commit(self, op: Operation, now_us: float):
        for spec in self.specs:
            if spec["issuer"] == op.kind.name:
                dt = sample_dist(spec["window_us"])
                ob = Obligation(
                    require = OpKind[spec["require"]],
                    targets = op.targets,
                    deadline_us = quantize(now_us + dt),
                    boost_factor = spec["priority_boost"]["boost_factor"],
                    hard_slot = spec["priority_boost"].get("hard_slot", False),
                )
                self._push_ob(ob)
                print(f"[{now_us:7.2f} us] OBLIG  created: {op.kind.name} -> {ob.require.name} by {ob.deadline_us:7.2f} us, target={_addr_str(ob.targets[0])}")
        if op.kind == OpKind.READ:
            t = op.targets[0]; seq_id = self.seq_by_block.get((t.die,t.plane,t.block))
            if seq_id is not None:
                self._refill_after_read(now_us, seq_id, t.page if t.page is not None else -1)

    def on_read_core_end(self, now_us: float, die:int, plane:int, block: Optional[int], page: Optional[int]):
        if not CFG["latch_guard"].get("fast_dout", False): return
        if block is None or page is None: return
        addr = Address(die, plane, block, page)
        for spec in self.specs:
            if spec["issuer"] == "READ" and spec["require"] == "DOUT":
                dt = sample_dist(spec["window_us"])
                ob = Obligation(
                    require = OpKind.DOUT,
                    targets = [addr],
                    deadline_us = quantize(now_us + dt),
                    boost_factor = spec["priority_boost"]["boost_factor"],
                    hard_slot = spec["priority_boost"].get("hard_slot", True),
                )
                self._push_ob(ob)
                print(f"[{now_us:7.2f} us] OBLIG  fast: READ(core_end) -> DOUT by {ob.deadline_us:7.2f} us, target={_addr_str(addr)}")

    def get_active_seq_info(self, die:int, plane:int) -> Optional[Tuple[int, Set[int]]]:
        keys = [k for k in self.addr.seq_locks.keys() if k[0]==die and k[1]==plane]
        if not keys: return None
        d,p,b = keys[0]; lk = self.addr.seq_locks[(d,p,b)]
        return b, set(lk["allowed_pages"])

    def pop_urgent(self, now_us: float, die:int, plane:int, horizon_us: float, earliest_start: float) -> Optional[Obligation]:
        if not self.heap: return None
        kept: List[_ObHeapItem] = []; chosen_seq: Optional[_ObHeapItem]=None; chosen_any: Optional[_ObHeapItem]=None
        now_us = quantize(now_us); earliest_start = quantize(earliest_start)
        boost = CFG.get("seq_read", {}).get("boost_window_us", 2.5)
        while self.heap:
            item = heapq.heappop(self.heap); ob = item.ob; tgt = ob.targets[0]
            same_plane = (tgt.die==die and tgt.plane==plane)
            in_horizon = ((ob.deadline_us - now_us) <= max(horizon_us, 0.0)) or ob.hard_slot
            feasible_time = (earliest_start <= ob.deadline_us) or ob.hard_slot
            if not (same_plane and in_horizon and feasible_time): 
                kept.append(item); 
                continue
            if ob.is_seq and ob.require == OpKind.READ:
                if (ob.deadline_us - now_us) <= boost or ob.hard_slot:
                    chosen_seq = item; break
                if not chosen_seq: chosen_seq = item
            if not chosen_any or item.deadline_us < chosen_any.deadline_us:
                chosen_any = item
            # FIXED PARENTHESIS:
            if self.heap and (self.heap[0].deadline_us - now_us) > horizon_us:
                break
        for it in kept: heapq.heappush(self.heap, it)
        return (chosen_seq or chosen_any).ob if (chosen_seq or chosen_any) else None

# ---------------- Policy Engine ----------------

class PolicyEngine:
    def __init__(self, cfg, addr_mgr: AddressManager, obl_mgr: ObligationManager):
        self.cfg=cfg; self.addr=addr_mgr; self.obl=obl_mgr

    def _score(self, op_name: str, phase_label: str, g: Dict[str,str], l: Dict[str,str]) -> float:
        w = self.cfg["weights"]["base"]["host"].get(op_name, 0.0)
        w *= self.cfg["weights"]["g_state"]["pgmable_ratio"].get(g["pgmable_ratio"], 1.0)
        w *= self.cfg["weights"]["g_state"]["readable_ratio"].get(g["readable_ratio"], 1.0)
        w *= self.cfg["weights"]["g_local"]["plane_busy_frac"].get(l["plane_busy_frac"], 1.0)
        near="MID_NEAR"
        if phase_label.endswith("START"): near="START_NEAR"
        elif phase_label.endswith("END"): near="END_NEAR"
        w *= self.cfg["weights"]["g_phase"].get(op_name, {}).get(near, 1.0)
        return w

    def _roulette(self, dist: Dict[str, float], allow: set) -> Optional[str]:
        items = [(name, p) for name, p in dist.items() if name in allow and p > 0.0]
        if not items: return None
        total = sum(p for _, p in items)
        r = random.random()*total; acc=0.0; pick=items[-1][0]
        for name, p in items:
            acc += p
            if r <= acc: pick=name; break
        return pick

    def _guard_pending_obligation(self, now_us: float, earliest_start: float, die:int, plane:int, kind_name: str) -> bool:
        ddl = self.obl.peek_next_deadline_for_plane(die, plane)
        if ddl is None:
            return True
        est = estimate_mean_duration(kind_name, self.cfg)
        start = max(now_us, earliest_start)
        end = start + est
        return end <= ddl

    def _guard_latch(self, now_us: float, earliest_start: float, die:int, plane:int, kind_name: str, block: Optional[int]) -> bool:
        if kind_name not in ("PROGRAM","ERASE"): 
            return True
        est = estimate_mean_duration(kind_name, self.cfg)
        start = max(now_us, earliest_start)
        end = start + est
        return not self.addr.query_guard(die, plane, start, end, block=block)

    def propose(self, now_us: float, hook: PhaseHook, g: Dict[str,str], l: Dict[str,str], earliest_start: float) -> Optional[Operation]:
        ob = self.obl.pop_urgent(now_us, hook.die, hook.plane, horizon_us=10.0, earliest_start=earliest_start)
        if ob:
            if self.addr.precheck(ob.require, ob.targets, start_hint=earliest_start):
                cfg_op = self.cfg["op_specs"][ob.require.name]
                op = build_operation(ob.require, cfg_op, ob.targets)
                op.meta["source"]="obligation"; op.meta["phase_key_used"]="(obligation)"
                if ob.is_seq: op.meta["seq_id"]=ob.seq_id
                return op
            else:
                self.obl.requeue(ob)

        allow = set(self.cfg["op_specs"].keys()) - {"DOUT"}
        seq_info = self.obl.get_active_seq_info(hook.die, hook.plane)
        if seq_info: allow.discard("READ")
        dist, used_key = get_phase_dist(self.cfg, hook.label)
        if dist is not None:
            if sum(dist.values()) <= 0.0:
                return None
            pick = self._roulette(dist, allow)
            if pick:
                if not self._guard_pending_obligation(now_us, earliest_start, hook.die, hook.plane, pick):
                    return None
                block = hook.block
                if not self._guard_latch(now_us, earliest_start, hook.die, hook.plane, pick, block):
                    return None
                fanout, interleave = get_phase_selection_override(self.cfg, hook.label, pick)
                kind = OpKind[pick]
                targets = self.addr.select(kind, hook.die, hook.plane, fanout=fanout, interleave=interleave)
                if targets and self.addr.precheck(kind, targets, start_hint=earliest_start):
                    op = build_operation(kind, self.cfg["op_specs"][pick], targets)
                    op.meta["source"]="policy.phase_conditional"; op.meta["phase_key_used"]=used_key
                    op.meta["fanout"]=fanout; op.meta["interleave"]=interleave
                    return op

        cand=[]
        for name in ["READ","PROGRAM","ERASE","SR"]:
            if seq_info and name=="READ": continue
            s=self._score(name, hook.label, g, l)
            if s>0: cand.append((name,s))
        if not cand: return None
        total=sum(s for _,s in cand); r=random.random()*total; acc=0.0; pick=cand[-1][0]
        for name,s in cand:
            acc+=s
            if r<=acc: pick=name; break

        if not self._guard_pending_obligation(now_us, earliest_start, hook.die, hook.plane, pick):
            return None
        block = hook.block
        if not self._guard_latch(now_us, earliest_start, hook.die, hook.plane, pick, block):
            return None

        kind=OpKind[pick]
        fanout, interleave = get_phase_selection_override(self.cfg, hook.label, pick)
        targets=self.addr.select(kind, hook.die, hook.plane, fanout=fanout, interleave=interleave)
        if not targets or not self.addr.precheck(kind, targets, start_hint=earliest_start): return None
        op=build_operation(kind, self.cfg["op_specs"][pick], targets)
        op.meta["source"]="policy.score_backoff"; op.meta["phase_key_used"]="(score_backoff)"
        op.meta["fanout"]=fanout; op.meta["interleave"]=interleave
        return op

# ---------------- Scheduler ----------------

class Scheduler:
    def __init__(self, cfg, addr_mgr: AddressManager, policy_engine: PolicyEngine, obl_mgr: ObligationManager):
        self.cfg=cfg; self.addr=addr_mgr; self.SPE=policy_engine; self.obl=obl_mgr
        self.now=0.0; self.ev=[]; self._seq=0
        self.stat_propose_calls = 0; self.stat_scheduled = 0
        self.count_by_source = defaultdict(int); self.count_by_kind = defaultdict(int)
        self._push(0.0, "QUEUE_REFILL", None)
        # for plane in range(self.addr.planes):
        #     self._push(0.0, "PHASE_HOOK", PhaseHook(0.0, "BOOT.START", 0, plane))

    def _push(self, t: float, typ: str, payload: Any):
        t = quantize(t)
        heapq.heappush(self.ev, (t, self._seq, typ, payload)); self._seq+=1

    def _unique_plane_keys(self, targets: List[Address]) -> List[Tuple[int,int,int]]:
        s=set()
        for a in targets: s.add((a.die, a.plane, a.block))
        return list(s)

    def _start_time_for_targets(self, targets: List[Address]) -> float:
        planes = {(a.die, a.plane) for a in targets}
        avail = [self.addr.available_at(d,p) for (d,p) in planes]
        return quantize(max([self.now] + avail))

    def _schedule_operation(self, op: Operation):
        start = self._start_time_for_targets(op.targets)
        dur = get_op_duration(op); end  = quantize(start + dur)
        for d,p,b in self._unique_plane_keys(op.targets):
            self.addr.reserve(d, p, start, end, block=b)
        for t in op.targets:
            hooks = make_phase_hooks(self.cfg, op, start, t.die, t.plane, t.block, t.page)
            for h in hooks: self._push(h.time_us, "PHASE_HOOK", h)
        self._push(start, "OP_START", op); self._push(end, "OP_END", op)

        if op.kind == OpKind.READ:
            core_end_rel = get_state_end_rel(op, "CORE_BUSY") or dur
            start_guard = quantize(start + core_end_rel)
            exp_dout = estimate_mean_duration("DOUT", self.cfg)
            margin = self.cfg["latch_guard"]["margin_us"]
            end_guard = quantize(end + exp_dout + margin)
            for t in op.targets:
                self.addr.add_guard(t.die, t.plane, start_guard, end_guard,
                                    scope=self.cfg["latch_guard"]["scope"], block=t.block, page=t.page)

        if op.kind == OpKind.DOUT:
            for t in op.targets:
                self.addr.update_guard_end(t.die, t.plane, t.page, t.block, end)

        first = op.targets[0]
        src = op.meta.get('source'); self.count_by_source[src]+=1
        self.count_by_kind[op.kind.name]+=1
        print(f"[{self.now:7.2f} us] SCHED  {op.kind.name:7s} tgt={len(op.targets)} start={start:7.2f} end={end:7.2f} 1st={_addr_str(first)} src={src} key={op.meta.get('phase_key_used')} fanout={op.meta.get('fanout')} seq={op.meta.get('seq_id')}")
        self.stat_scheduled += 1

    def _update_emergency_gc(self, die:int, plane:int, g: Dict[str,str]):
        self.addr.set_emergency_gc(die, plane, g.get("pgmable_ratio") == "low")

    def run_until(self, t_end: float):
        t_end = quantize(t_end)
        while self.ev and self.ev[0][0] <= t_end:
            self.now, _, typ, payload = heapq.heappop(self.ev)
            if typ=="QUEUE_REFILL":
                for plane in range(self.addr.planes):
                    self._push(self.now, "PHASE_HOOK", PhaseHook(self.now, "REFILL.NUDGE", 0, plane))
                self._push(quantize(self.now + self.cfg["policy"]["queue_refill_period_us"]), "QUEUE_REFILL", None)

            elif typ=="PHASE_HOOK":
                hook: PhaseHook = payload
                if hook.label == "READ.CORE_BUSY.END" and self.cfg["latch_guard"].get("fast_dout", False):
                    self.obl.on_read_core_end(self.now, hook.die, hook.plane, hook.block, hook.page)

                earliest_start = self.addr.available_at(hook.die, hook.plane)
                g,l = self.addr.observe_states(hook.die, hook.plane, self.now)
                self._update_emergency_gc(hook.die, hook.plane, g)
                self.stat_propose_calls += 1
                op = self.SPE.propose(self.now, hook, g, l, earliest_start)
                if op: self._schedule_operation(op)

            elif typ=="OP_START":
                op: Operation = payload
                print(f"[{self.now:7.2f} us] START  {op.kind.name:7s} target={_addr_str(op.targets[0])} src={op.meta.get('source')}")

            elif typ=="OP_END":
                op: Operation = payload
                print(f"[{self.now:7.2f} us] END    {op.kind.name:7s} target={_addr_str(op.targets[0])}")
                self.addr.commit(op); self.obl.on_commit(op, self.now)

        print(f"\n=== Stats ===")
        print(f"propose calls : {self.stat_propose_calls}")
        print(f"scheduled ops : {self.stat_scheduled}")
        if self.stat_propose_calls:
            print(f"accept ratio  : {100.0*self.stat_scheduled/self.stat_propose_calls:.1f}%")
        print("\n-- Scheduled by source --")
        for k in sorted(self.count_by_source.keys()):
            print(f"{k:24s}: {self.count_by_source[k]}")
        print("\n-- Scheduled by kind --")
        for k in sorted(self.count_by_kind.keys()):
            print(f"{k:8s}: {self.count_by_kind[k]}")
        if hasattr(self.obl, "created_by_kind"):
            print("\n-- Obligations created --")
            for k in sorted(self.obl.created_by_kind.keys()):
                print(f"{k:8s}: {self.obl.created_by_kind[k]}")

# ---------------- Demo ----------------

def main():
    random.seed(CFG["rng_seed"])
    addr = AddressManager(CFG); obl  = ObligationManager(CFG["obligations"], addr, CFG)
    spe  = PolicyEngine(CFG, addr, obl); sch  = Scheduler(CFG, addr, spe, obl)
    obl.start_seq_read(now_us=0.0, die=0, plane=0, block=0, start_page=0, count=20, window=CFG["seq_read"]["window"])
    print("=== NAND Sequence Generator Demo (P6: LatchGuard + fixed parenthesis) ===")
    sch.run_until(CFG["policy"]["run_until_us"])
    print("=== Done ===")

if __name__ == "__main__":
    main()
