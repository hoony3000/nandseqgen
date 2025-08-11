# op_spec.py (타임라인/훅 생성)
from typing import Dict, List
from models import Operation, StateSeg, PhaseHook, OpKind
from dist import sample_dist

def build_operation(kind: OpKind, cfg_op: Dict, targets):
    states=[]
    for s in cfg_op["states"]:
        dur = sample_dist(s["dist"])
        states.append(StateSeg(name=s["name"], dur_us=dur))
    return Operation(kind=kind, targets=targets, states=states)

def make_phase_hooks(op: Operation, start_us: float, cfg_op: Dict, die:int, plane:int)->List[PhaseHook]:
    hooks_cfg = cfg_op.get("hooks", [])
    offset = cfg_op.get("phase_offset_us",{}).get("default",0.0)
    t = start_us + offset
    hooks=[]
    # 각 state의 절대 시작/중간/끝 시각 계산
    cur = t
    for seg in op.states:
        seg_start = cur
        seg_mid   = cur + seg.dur_us*0.5
        seg_end   = cur + seg.dur_us
        for rule in hooks_cfg:
            if seg.name not in rule["states"]: 
                continue
            jitter = rule.get("jitter_us", 0.0)
            if rule["when"]=="STATE_START":
                hooks.append(PhaseHook(time_us=seg_start+jitter*rand_sign(), label=f"{op.kind.name}.{seg.name}.START", die=die, plane=plane))
            elif rule["when"]=="STATE_MID":
                hooks.append(PhaseHook(time_us=seg_mid+jitter*rand_sign(),   label=f"{op.kind.name}.{seg.name}.MID",   die=die, plane=plane))
            elif rule["when"]=="STATE_END":
                hooks.append(PhaseHook(time_us=seg_end+jitter*rand_sign(),   label=f"{op.kind.name}.{seg.name}.END",   die=die, plane=plane))
        cur = seg_end
    return hooks

def rand_sign():
    import random
    return random.uniform(-1.0,1.0)