# policy_engine.py (SPE)
import random
from typing import Optional
from models import Operation, OpKind, Address
from op_spec import build_operation
from dist import sample_dist

class PolicyEngine:
    def __init__(self, cfg, addr_mgr, dep_rules, obligation_mgr):
        self.cfg = cfg
        self.addr = addr_mgr
        self.dep = dep_rules
        self.obl = obligation_mgr

    def _sample_class(self, global_state):
        items = list(self.cfg["mixture"]["classes"].items())
        names, probs = zip(*items)
        r = random.random()*sum(probs)
        acc=0
        for n,p in items:
            acc+=p
            if r<=acc: 
                return n
        return names[-1]

    def _score(self, op_name, phase_label, global_state, local_state):
        w = self.cfg["weights"]["base"][global_state["cls"]].get(op_name, 0.0)
        # 단순 예시: g_state/g_local/g_phase 곱
        def gw(table, key, sub):
            return self.cfg["weights"][table].get(key,{}).get(sub,1.0)
        w *= gw("g_state","pgmable_ratio",global_state["pgmable_ratio"])
        w *= gw("g_state","readable_ratio",global_state["readable_ratio"])
        w *= gw("g_local","plane_busy_frac",local_state["plane_busy_frac"])
        near = "MID_NEAR"
        if phase_label.endswith("START"): near="START_NEAR"
        elif phase_label.endswith("END"): near="END_NEAR"
        w *= self.cfg["weights"]["g_phase"].get(op_name,{}).get(near,1.0)
        return w

    def propose(self, now_us, hook, global_state, local_state)->Optional[Operation]:
        # 0) 의무 우선 검토
        urgent = self.obl.urgent(now_us)
        if urgent:
            ob = urgent[0]
            # 대상 주소 가용성/의존성 사전체크(간소화)
            if self.addr.precheck(ob.require, ob.targets):
                cfg_op = self.cfg["op_specs"][ob.require.name]
                return build_operation(ob.require, cfg_op, ob.targets)

        # 1) 클래스 샘플
        global_state["cls"] = self._sample_class(global_state)
        # 2) 후보 점수화 → 룰렛
        cand=[]
        for op_name in self.cfg["op_specs"].keys():
            if op_name=="DOUT":   # 보통 의무로만 발생
                continue
            score = self._score(op_name, hook.label, global_state, local_state)
            if score>0:
                cand.append((op_name, score))
        if not cand: return None
        total = sum(s for _,s in cand)
        r = random.random()*total; acc=0
        pick = cand[-1][0]
        for name, s in cand:
            acc+=s
            if r<=acc: 
                pick=name; break

        kind = OpKind[pick]
        targets = self.addr.select(kind)        # address_policy 반영
        if not targets: return None
        cfg_op = self.cfg["op_specs"][pick]
        op = build_operation(kind, cfg_op, targets)
        # 사전검사(의존성/락/plane여유)
        if not self.addr.precheck(kind, targets): 
            return None
        return op