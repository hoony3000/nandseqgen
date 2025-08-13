# nandsim_demo_p2_seq.py — Sequential READ window + soft-lock + emergency GC + phase-conditional etc.
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
        "lookahead_k": 4,
        "run_until_us": 220.0,
    },
    "seq_read": {
        "window": 4,                 # outstanding READ obligations
        "deadline_spacing_us": 2.0,  # spacing between READ deadlines inside window
        "softlock_grace_us": 8.0,    # soft-lock TTL extension after last outstanding deadline
        "boost_window_us": 2.5,      # pop_urgent boost horizon for seq READ (hard-slot)
    },
    # backoff weights (phase-conditional 미존재/0합일 때 사용)
    "weights": {
        "base": {"host": {"READ": 0.85, "PROGRAM": 0.10, "ERASE": 0.05, "SR": 0.00, "RESET": 0.00, "DOUT": 0.00}},
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
    "phase_conditional": {
        "READ.ISSUE":              {"READ": 0.00, "PROGRAM": 0.80, "ERASE": 0.20},
        "READ.CORE_BUSY.START":    {"READ": 0.05, "PROGRAM": 0.85, "ERASE": 0.10},
        "READ.CORE_BUSY.MID":      {"READ": 0.10, "PROGRAM": 0.70, "ERASE": 0.20},
        "READ.DATA_OUT.START":     {"READ": 0.60, "PROGRAM": 0.25, "ERASE": 0.15},
        "PROGRAM.ISSUE":           {"READ": 0.50, "PROGRAM": 0.25, "ERASE": 0.25},
        "PROGRAM.CORE_BUSY.END":   {"READ": 0.60, "PROGRAM": 0.10, "ERASE": 0.30},
        "DEFAULT":                 {"READ": 0.60, "PROGRAM": 0.30, "ERASE": 0.10},
    },
    "selection": {
        "defaults": {
            "READ":    {"fanout": 1, "interleave": True},
            "PROGRAM": {"fanout": 1, "interleave": True},
            "ERASE":   {"fanout": 1, "interleave": False},
        },
        "phase_overrides": {
            "READ.CORE_BUSY.START": {"fanout": 2, "interleave": True},
        }
    },
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
    "topology": {"dies": 1, "planes_per_die": 4, "blocks_per_plane": 8, "pages_per_block": 16},
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

def rand_jitter(ampl_us: float) -> float:
    if ampl_us<=0: return 0.0
    return random.uniform(-ampl_us, ampl_us)

def parse_hook_key(label: str):
    parts = label.split(".")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], None
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

def get_op_duration(op: Operation) -> float:
    return sum(seg.dur_us for seg in op.states)

# ---------------------------- OpSpec: build ops & hooks ----------------------------

def build_operation(kind: OpKind, cfg_op: Dict[str, Any], targets: List[Address]) -> Operation:
    states=[]
    for s in cfg_op["states"]:
        dur = sample_dist(s["dist"])
        states.append(StateSeg(name=s["name"], dur_us=dur))
    return Operation(kind=kind, targets=targets, states=states)

def make_phase_hooks(cfg: Dict[str,Any], op: Operation, start_us: float, die:int, plane:int) -> List[PhaseHook]:
    cfg_op = cfg["op_specs"][op.kind.name]
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
                hooks.append(PhaseHook(time_us=quantize(seg_start + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.START", die=die, plane=plane))
            elif rule["when"]=="STATE_MID":
                hooks.append(PhaseHook(time_us=quantize(seg_mid   + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.MID",   die=die, plane=plane))
            elif rule["when"]=="STATE_END":
                hooks.append(PhaseHook(time_us=quantize(seg_end   + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.END",   die=die, plane=plane))
        cur = seg_end
    return hooks

# ---------------------------- Address Manager ----------------------------

class AddressManager:
    def __init__(self, cfg: Dict[str, Any]):
        topo = cfg["topology"]
        self.cfg = cfg
        self.dies = topo["dies"]
        self.planes = topo["planes_per_die"]
        self.pages_per_block = topo["pages_per_block"]
        self.blocks_per_plane = topo["blocks_per_plane"]

        self.available: Dict[Tuple[int,int], float] = {(0,p): 0.0 for p in range(self.planes)}
        self.cursors: Dict[Tuple[int,int], List[int]] = {(0,p): [0,0,0] for p in range(self.planes)}  # [block, next_pgm_page, next_rd_page]
        self.programmed: Dict[Tuple[int,int], Set[Tuple[int,int]]] = {(0,p): set() for p in range(self.planes)}
        self.resv: Dict[Tuple[int,int], List[Tuple[float,float,Optional[int]]]] = {(0,p): [] for p in range(self.planes)}

        # soft-locks for seq read: (die,plane,block) -> {"expiry": float, "allowed_pages": set}
        self.seq_locks: Dict[Tuple[int,int,int], Dict[str,Any]] = {}
        # emergency GC per plane
        self.emgc: Dict[Tuple[int,int], bool] = {(0,p): False for p in range(self.planes)}

    def set_emergency_gc(self, die:int, plane:int, active: bool):
        self.emgc[(die,plane)] = bool(active)

    def set_seq_lock(self, die:int, plane:int, block:int, expiry_us: float, allowed_pages: Set[int]):
        key=(die,plane,block)
        self.seq_locks[key] = {"expiry": quantize(expiry_us), "allowed_pages": set(allowed_pages)}

    def update_seq_lock_pages(self, die:int, plane:int, block:int, allowed_pages: Set[int], extend_expiry: Optional[float]=None):
        key=(die,plane,block)
        lk = self.seq_locks.get(key)
        if not lk:
            return
        lk["allowed_pages"] = set(allowed_pages)
        if extend_expiry is not None:
            lk["expiry"] = quantize(max(lk["expiry"], extend_expiry))

    def clear_seq_lock(self, die:int, plane:int, block:int):
        self.seq_locks.pop((die,plane,block), None)

    # ---- observation / selection / checks ----

    def available_at(self, die:int, plane:int) -> float:
        return self.available[(die,plane)]

    def observe_states(self, die:int, plane:int, now_us: float):
        prog = len(self.programmed[(die,plane)])
        # naive buckets for demo
        pgmable_ratio  = "mid" if prog < 10 else "low"   # fewer programmed pages => "mid" (room to program)
        readable_ratio = "mid" if prog > 0 else "low"
        plane_busy_frac = "high" if self.available_at(die,plane) > now_us else "low"
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls": "host"},
                {"plane_busy_frac": plane_busy_frac})

    def _plane_ring(self, start_plane:int, fanout:int, interleave:bool) -> List[int]:
        if fanout <= 1:
            return [start_plane]
        ring = []
        P = self.planes
        idx = start_plane
        for _ in range(fanout):
            ring.append(idx)
            idx = (idx + 1) % P
        return ring

    def select(self, kind: OpKind, die:int, plane:int, fanout:int=1, interleave:bool=True) -> List[Address]:
        planes = self._plane_ring(plane, fanout, interleave)
        targets: List[Address] = []
        for pl in planes:
            if kind == OpKind.READ:
                # If seq-lock exists, pick first allowed page if any; else fallback to smallest programmed
                # (to reduce precheck rejections)
                # find any lock on this plane
                locks_here = [k for k in self.seq_locks.keys() if k[0]==die and k[1]==pl]
                page = None; block = 0
                if locks_here:
                    d,p,b = locks_here[0]
                    lk = self.seq_locks[(d,p,b)]
                    if lk["allowed_pages"]:
                        page = sorted(lk["allowed_pages"])[0]
                        block = b
                if page is None:
                    prog = sorted(self.programmed[(die,pl)])
                    if prog:
                        block, page = prog[0]
                    else:
                        block, page = 0, 0
                targets.append(Address(die, pl, block=block, page=page))
            elif kind == OpKind.DOUT:
                raise RuntimeError("DOUT selection must be provided by obligation targets")
            elif kind == OpKind.PROGRAM:
                b, pgm_p, _ = self.cursors[(die,pl)]
                targets.append(Address(die, pl, block=b, page=pgm_p))
                # advance cursor
                pgm_p += 1
                if pgm_p >= self.pages_per_block:
                    pgm_p = 0
                    b = (b+1) % self.blocks_per_plane
                self.cursors[(die,pl)] = [b, pgm_p, self.cursors[(die,pl)][2]]
            elif kind == OpKind.ERASE:
                b = self.cursors[(die,pl)][0]
                targets.append(Address(die, pl, block=b, page=None))
            else:
                targets.append(Address(die, pl, block=0, page=0))
        return targets

    def precheck(self, kind: OpKind, targets: List[Address], start_hint: float) -> bool:
        start_hint = quantize(start_hint)
        end_hint = start_hint
        seen_planes = set()
        for t in targets:
            keyp = (t.die, t.plane)
            if keyp not in seen_planes:
                seen_planes.add(keyp)
                for (s,e,_) in self.resv[keyp]:
                    if not (end_hint <= s or e <= start_hint):
                        return False
            # soft-lock rules per block
            if t.block is not None:
                keyb = (t.die, t.plane, t.block)
                lk = self.seq_locks.get(keyb)
                if lk and start_hint < lk["expiry"]:
                    # READ: allow only pages within allowed set
                    if kind == OpKind.READ:
                        if t.page is None or t.page not in lk["allowed_pages"]:
                            return False
                    # PROGRAM/ERASE blocked unless emergency GC
                    if kind in (OpKind.PROGRAM, OpKind.ERASE):
                        if not self.emgc.get((t.die, t.plane), False):
                            return False
        return True

    def reserve(self, die:int, plane:int, start:float, end:float, block: Optional[int]=None):
        start = quantize(start); end = quantize(end)
        self.available[(die,plane)] = max(self.available[(die,plane)], end)
        self.resv[(die,plane)].append((start, end, block))

    def commit(self, op: Operation):
        t = op.targets[0]
        key = (t.die, t.plane)
        if op.kind == OpKind.PROGRAM and t.page is not None:
            self.programmed[key].add((t.block, t.page))
        elif op.kind == OpKind.ERASE:
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
    is_seq: bool = False
    seq_id: Optional[int] = None

@dataclass
class SeqReadContext:
    seq_id: int
    die: int
    plane: int
    block: int
    start_page: int
    total_pages: int
    next_page: int
    window: int
    active_pages: Set[int] = field(default_factory=set)
    finished: bool = False

class ObligationManager:
    def __init__(self, cfg_list: List[Dict[str,Any]], addr: AddressManager, cfg: Dict[str,Any]):
        self.specs = cfg_list
        self.addr = addr
        self.cfg = cfg
        self.heap: List[_ObHeapItem] = []
        self._seq = 0
        self._obseq = 0
        # seq contexts
        self.seq_ctx_by_id: Dict[int, SeqReadContext] = {}
        self.seq_by_block: Dict[Tuple[int,int,int], int] = {}

    # ---- helpers ----
    def _push_ob(self, ob: Obligation):
        heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._obseq, ob=ob))
        self._obseq += 1

    def _make_deadline(self, base: float, idx: int=0, spacing: float=0.0) -> float:
        return quantize(base + idx * spacing)

    # ---- public APIs ----
    def start_seq_read(self, now_us: float, die:int, plane:int, block:int, start_page:int, count:int, window: Optional[int]=None):
        W = window if window is not None else self.cfg["seq_read"]["window"]
        seq_id = self._seq; self._seq += 1
        ctx = SeqReadContext(seq_id=seq_id, die=die, plane=plane, block=block,
                             start_page=start_page, total_pages=count,
                             next_page=start_page, window=W)
        self.seq_ctx_by_id[seq_id] = ctx
        self.seq_by_block[(die,plane,block)] = seq_id

        spacing = self.cfg["seq_read"]["deadline_spacing_us"]
        # seed initial window
        to_seed = min(W, count)
        allowed: Set[int] = set()
        for i in range(to_seed):
            page = ctx.next_page
            ctx.active_pages.add(page); allowed.add(page)
            ctx.next_page += 1
            dl = self._make_deadline(now_us, i, spacing)
            ob = Obligation(require=OpKind.READ, targets=[Address(die,plane,block,page)],
                            deadline_us=dl, boost_factor=1.0, hard_slot=True,
                            is_seq=True, seq_id=seq_id)
            self._push_ob(ob)
        # set soft-lock with grace
        expiry = self._make_deadline(now_us, to_seed, spacing) + self.cfg["seq_read"]["softlock_grace_us"]
        self.addr.set_seq_lock(die, plane, block, expiry_us=expiry, allowed_pages=allowed)
        print(f"[{now_us:7.2f} us] SEQRD  start: die{die}/pl{plane}/b{block} p{start_page}..{start_page+count-1} W={W}")

    def _refill_after_read(self, now_us: float, seq_id: int, page_done: int):
        ctx = self.seq_ctx_by_id.get(seq_id)
        if not ctx or ctx.finished:
            return
        if page_done in ctx.active_pages:
            ctx.active_pages.remove(page_done)
        # refill if window allows and there are remaining pages
        if ctx.next_page < ctx.start_page + ctx.total_pages:
            # keep window size
            while len(ctx.active_pages) < ctx.window and ctx.next_page < ctx.start_page + ctx.total_pages:
                p = ctx.next_page
                ctx.active_pages.add(p)
                ctx.next_page += 1
                # deadline: from now or from last? Use now-based spacing for simplicity
                dl = self._make_deadline(now_us, 0, 0.0) + self.cfg["seq_read"]["deadline_spacing_us"]
                ob = Obligation(require=OpKind.READ, targets=[Address(ctx.die, ctx.plane, ctx.block, p)],
                                deadline_us=dl, boost_factor=1.0, hard_slot=True,
                                is_seq=True, seq_id=seq_id)
                self._push_ob(ob)
        # update soft-lock allowed pages & expiry
        allowed = set(ctx.active_pages)
        grace = self.cfg["seq_read"]["softlock_grace_us"]
        expiry = quantize(now_us + grace)
        self.addr.update_seq_lock_pages(ctx.die, ctx.plane, ctx.block, allowed_pages=allowed, extend_expiry=expiry)

        # finish condition (READ 측면): no more active and next_page reached end
        if not ctx.active_pages and ctx.next_page >= ctx.start_page + ctx.total_pages:
            ctx.finished = True
            # keep soft-lock a bit (until grace); let DOUTs finish by deadline+grace naturally
            print(f"[{now_us:7.2f} us] SEQRD  finished READ: die{ctx.die}/pl{ctx.plane}/b{ctx.block}")
            # Optionally clear when DOUT completes; for demo we retain until grace expiry

    def on_commit(self, op: Operation, now_us: float):
        # 1) generic issuer->require rules (READ -> DOUT)
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

        # 2) seq-read bookkeeping on READ commit
        if op.kind == OpKind.READ:
            t = op.targets[0]
            seq_id = self.seq_by_block.get((t.die, t.plane, t.block))
            if seq_id is not None:
                self._refill_after_read(now_us, seq_id, t.page if t.page is not None else -1)

    def get_active_seq_info(self, die:int, plane:int) -> Optional[Tuple[int, Set[int]]]:
        # return (block, allowed_pages) if a seq-lock exists on this plane
        keys = [k for k in self.addr.seq_locks.keys() if k[0]==die and k[1]==plane]
        if not keys: return None
        d,p,b = keys[0]
        lk = self.addr.seq_locks[(d,p,b)]
        return b, set(lk["allowed_pages"])

    def pop_urgent(self, now_us: float, die:int, plane:int,
                   horizon_us: float, earliest_start: float) -> Optional[Obligation]:
        """Prefer seq READ obligations for this plane; else earliest feasible obligation."""
        if not self.heap: 
            return None
        kept: List[_ObHeapItem] = []
        chosen_seq: Optional[_ObHeapItem] = None
        chosen_any: Optional[_ObHeapItem] = None
        now_us = quantize(now_us)
        earliest_start = quantize(earliest_start)
        boost = self.cfg.get("seq_read", {}).get("boost_window_us", 2.5)

        while self.heap:
            item = heapq.heappop(self.heap)
            ob = item.ob
            tgt = ob.targets[0]
            same_plane = (tgt.die == die and tgt.plane == plane)
            in_horizon = ((ob.deadline_us - now_us) <= max(horizon_us, 0.0)) or ob.hard_slot
            feasible_time = (earliest_start <= ob.deadline_us)
            if not (same_plane and in_horizon and feasible_time):
                kept.append(item); continue
            # candidate
            if ob.is_seq and ob.require == OpKind.READ:
                # prefer seq READ if within boost window
                if (ob.deadline_us - now_us) <= boost or ob.hard_slot:
                    chosen_seq = item
                    break
                if not chosen_seq:
                    chosen_seq = item  # keep best seq-read encountered
            if not chosen_any:
                chosen_any = item
            else:
                # keep the earliest deadline among non-seq candidates
                if item.deadline_us < chosen_any.deadline_us:
                    kept.append(chosen_any); chosen_any = item
                else:
                    kept.append(item)
            # stop early if the peeked item is much later than now + horizon (heap is sorted)
            if (self.heap and self.heap[0].deadline_us - now_us) > horizon_us:
                break

        # restore heap
        for it in kept:
            heapq.heappush(self.heap, it)

        picked = chosen_seq or chosen_any
        if picked:
            return picked.ob
        return None

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

    def _roulette(self, dist: Dict[str, float], allow: set) -> Optional[str]:
        items = [(name, p) for name, p in dist.items() if name in allow and p > 0.0]
        if not items: 
            return None
        total = sum(p for _, p in items)
        r = random.random() * total
        acc = 0.0
        pick = items[-1][0]
        for name, p in items:
            acc += p
            if r <= acc:
                pick = name; break
        return pick

    def propose(self, now_us: float, hook: PhaseHook,
                global_state: Dict[str,str], local_state: Dict[str,str],
                earliest_start: float) -> Optional[Operation]:
        # 0) Obligation 우선 (plane/time 필터)
        ob = self.obl.pop_urgent(now_us, hook.die, hook.plane,
                                 horizon_us=10.0, earliest_start=earliest_start)
        if ob:
            if self.addr.precheck(ob.require, ob.targets, start_hint=earliest_start):
                cfg_op = self.cfg["op_specs"][ob.require.name]
                op = build_operation(ob.require, cfg_op, ob.targets)
                op.meta["source"]="obligation"
                op.meta["phase_key_used"]="(obligation)"
                if ob.is_seq: op.meta["seq_id"]=ob.seq_id
                return op

        # 1) Phase-conditional 분포 (단, 활성 seq가 있으면 policy READ는 회피)
        allow = set(self.cfg["op_specs"].keys()) - {"DOUT"}
        seq_info = self.obl.get_active_seq_info(hook.die, hook.plane)
        if seq_info:
            allow.discard("READ")  # READ는 의무로 처리; policy READ 회피
        dist, used_key = get_phase_dist(self.cfg, hook.label)
        fanout, interleave = get_phase_selection_override(self.cfg, hook.label, "<DEFAULT>")
        if dist:
            pick = self._roulette(dist, allow)
            if pick:
                fanout, interleave = get_phase_selection_override(self.cfg, hook.label, pick)
                kind = OpKind[pick]
                targets = self.addr.select(kind, hook.die, hook.plane, fanout=fanout, interleave=interleave)
                if targets and self.addr.precheck(kind, targets, start_hint=earliest_start):
                    op = build_operation(kind, self.cfg["op_specs"][pick], targets)
                    op.meta["source"]="policy.phase_conditional"
                    op.meta["phase_key_used"]=used_key
                    op.meta["fanout"]=fanout; op.meta["interleave"]=interleave
                    return op

        # 2) Backoff: score 방식
        cand = []
        for name in ["READ", "PROGRAM", "ERASE"]:
            if seq_info and name=="READ":
                continue  # 활성 seq가 있으면 READ는 의무에 맡긴다
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
        fanout, interleave = get_phase_selection_override(self.cfg, hook.label, pick)
        targets = self.addr.select(kind, hook.die, hook.plane, fanout=fanout, interleave=interleave)
        if not targets:
            return None
        if not self.addr.precheck(kind, targets, start_hint=earliest_start):
            return None
        op = build_operation(kind, self.cfg["op_specs"][pick], targets)
        op.meta["source"]="policy.score_backoff"
        op.meta["phase_key_used"]="(score_backoff)"
        op.meta["fanout"]=fanout; op.meta["interleave"]=interleave
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
        for plane in range(self.addr.planes):
            self._push(0.0, "PHASE_HOOK", PhaseHook(0.0, "BOOT.START", 0, plane))

    def _push(self, t: float, typ: str, payload: Any):
        t = quantize(t)
        heapq.heappush(self.ev, (t, self._seq, typ, payload)); self._seq+=1

    def _unique_plane_keys(self, targets: List[Address]) -> List[Tuple[int,int,int]]:
        s=set()
        for a in targets:
            s.add((a.die, a.plane, a.block))
        return list(s)

    def _start_time_for_targets(self, targets: List[Address]) -> float:
        planes = {(a.die, a.plane) for a in targets}
        avail = [self.addr.available_at(d,p) for (d,p) in planes]
        return quantize(max([self.now] + avail))

    def _schedule_operation(self, op: Operation):
        start = self._start_time_for_targets(op.targets)
        dur = get_op_duration(op)
        end  = quantize(start + dur)
        for d,p,b in self._unique_plane_keys(op.targets):
            self.addr.reserve(d, p, start, end, block=b)
        self._push(start, "OP_START", op)
        self._push(end,   "OP_END",   op)
        for d,p,_ in self._unique_plane_keys(op.targets):
            hooks = make_phase_hooks(self.cfg, op, start, d, p)
            for h in hooks:
                self._push(h.time_us, "PHASE_HOOK", h)
        first = op.targets[0]
        print(f"[{self.now:7.2f} us] SCHED  {op.kind.name:7s} tgt={len(op.targets)} start={start:7.2f} end={end:7.2f} 1st={_addr_str(first)} src={op.meta.get('source')} key={op.meta.get('phase_key_used')} fanout={op.meta.get('fanout')} seq={op.meta.get('seq_id')}")
        self.stat_scheduled += 1

    def _update_emergency_gc(self, die:int, plane:int, global_state: Dict[str,str]):
        # crude rule: pgmable_ratio == "low" -> emergency GC on
        emgc = (global_state.get("pgmable_ratio") == "low")
        self.addr.set_emergency_gc(die, plane, emgc)

    def run_until(self, t_end: float):
        t_end = quantize(t_end)
        while self.ev and self.ev[0][0] <= t_end:
            self.now, _, typ, payload = heapq.heappop(self.ev)
            if typ=="QUEUE_REFILL":
                for plane in range(self.addr.planes):
                    self._push(self.now, "PHASE_HOOK", PhaseHook(self.now, "REFILL.NUDGE", 0, plane))
                nxt = quantize(self.now + self.cfg["policy"]["queue_refill_period_us"])
                self._push(nxt, "QUEUE_REFILL", None)

            elif typ=="PHASE_HOOK":
                hook: PhaseHook = payload
                earliest_start = self.addr.available_at(hook.die, hook.plane)
                global_state, local_state = self.addr.observe_states(hook.die, hook.plane, self.now)
                # Update emergency GC mode for this plane
                self._update_emergency_gc(hook.die, hook.plane, global_state)
                self.stat_propose_calls += 1
                op = self.SPE.propose(self.now, hook, global_state, local_state, earliest_start)
                if op:
                    self._schedule_operation(op)

            elif typ=="OP_START":
                op: Operation = payload
                first = op.targets[0]
                print(f"[{self.now:7.2f} us] START  {op.kind.name:7s} target={_addr_str(first)}")

            elif typ=="OP_END":
                op: Operation = payload
                first = op.targets[0]
                print(f"[{self.now:7.2f} us] END    {op.kind.name:7s} target={_addr_str(first)}")
                self.addr.commit(op)
                self.obl.on_commit(op, self.now)

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
    obl  = ObligationManager(CFG["obligations"], addr, CFG)
    spe  = PolicyEngine(CFG, addr, obl)
    sch  = Scheduler(CFG, addr, spe, obl)
    run_until = CFG["policy"]["run_until_us"]

    # --- Demo: host sequential READ request (20 pages from block 0, page 0) on die0/plane0 ---
    obl.start_seq_read(now_us=0.0, die=0, plane=0, block=0, start_page=0, count=20, window=CFG["seq_read"]["window"])

    print("=== NAND Sequence Generator Demo (P2: seq-read window + softlock + emGC) ===")
    sch.run_until(run_until)
    print("=== Done ===")

if __name__ == "__main__":
    main()
