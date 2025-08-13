# nandsim_demo.py 나만의 질문과 이해

## 질문 및 답변
- Q : available_at 은 언제 업데이트 되나?
- A : _schedule_operation 시 addr.reserve 를 통해서 업데이트
- Q : propose 할 때 모든 context check 다 하나?
- A : self.now, hook, global_state, local_state 을 참고해서 제안
- Q : global_state, local_state 는 언제 업데이트 되는가?
- A : 'PHASE_HOOK' event 처리 시 맨 첫번째로 업데이트 후 propose 실행. 이후 addr.observe_states(die,plane) 에 의해서
- Q : global_state 가 포함하고 있는 값들은?
- A : pgmable_ratio, readable_ratio
- Q : local_state 가 포함하고 있는 값들은?
- A : plane-bus_frac. 미구현 상태
- Q : propose 된 operation 은 어느 시점에 commit 되나?
- A : _schedule_operation 에 의해 예약됨
  - self._schedule_operation(op, hook.die, hook.plane)
  -     def _schedule_operation(self, op: Operation, die:int, plane:int):
        # Determine start considering plane availability
        start = max(self.now, self.addr.available_at(die,plane))
        dur = sum(seg.dur_us for seg in op.states)
        end  = start + dur
        self.addr.reserve(die, plane, start, end)
        self._push(start, "OP_START", (op, die, plane))
        self._push(end,   "OP_END",   (op, die, plane))
        # Generate hooks at real absolute times
        hooks = make_phase_hooks(op, start, self.cfg["op_specs"][op.kind.name], die, plane)
        for h in hooks:
            self._push(h.time_us, "PHASE_HOOK", h)
  - operation 이 제안됨 후에는 target die, plane 에서 가용 가능한 시점 addr.available_at(die,plane) 과 현재 시간 self.now 를 비교해 가장 빠른 시간으로 reserve(die, plane, start, end) 한다. 그 다음은 self.ev 에 'OP_START', 'OP_END', 'PHASE_HOOK' 을 추가 한다.
  - Q : reserve 함수의 역할은? reserve 후에 바로 'OP_START', 'OP_END' 를 추가하는데, busy state 를 점유하기 위한 것인가?
  - A : self.available[(die,plane)] 의 값을 업데이트 한다. busy 상태로 잠그는 역할
    -  def reserve(self, die:int, plane:int, start:float, end:float):<br>
        self.available[(die,plane)] = max(self.available[(die,plane)], end)
  - Q : addr.available_at(die,plane) 가 반환하는 값은?
  - A : self.available[(die,plane)]


-       def observe_states(self, die:int, plane:int) -> Tuple[Dict[str,str], Dict[str,str]]:
        # Map observed counters to simple bucket names
        prog = len(self.programmed[(die,plane)])
        # naive ratios for demo
        pgmable_ratio = "mid" if prog < 10 else "low"
        readable_ratio = "mid" if prog > 0 else "low"
        plane_busy_frac = "low"  # not tracking real busy ratio in demo
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls": "host"},
                {"plane_busy_frac": plane_busy_frac})
    
- propose 에서 인자로 받는 것 : self.SPE.propose(self.now, hook:PhaseHook, global_state, local_state)
  - func signature : def propose(self, now_us: float, hook: PhaseHook, global_state: Dict[str,str], local_state: Dict[str,str]) -> Optional[Operation]:
  - self.now : 현 시각 기준 nand 의 상태를 조건부로 operation 확률 샘플링 필요
  - hook: propose 는 self.ev 가 'PHASE_HOOK' 일 경우에만 실행되기 때문에, hook 을 payload 로 받는다
    - @dataclass<br>
    class PhaseHook:<br>
    time_us: float<br>
    label: str<br>
    die:int; plane:int<br>
    - time_us : 상대 시간? make_phase_hooks 이후에 heapppush 할 때만 쓰인다.
    - Q : heappop 할 때의 시점과, time_us 의 시점이 차이날텐데 clock jump 는 어떻게 이루어지나?
    - A : self.now 의 설정은 heappop 할 떄 self.ev[0] 값으로 입력
      - self.now, _, typ, payload = heapq.heappop(self.ev)
      - self.ev : (time, seq, type, payload)
  - global_state, loal_state : 'PHASE_HOOK' 일 경우 맨 첫번째로 addr.observe_states(hook.die, hook.plane) 로 전달 받는다.
    - global_state, local_state = self.addr.observe_states(hook.die, hook.plane)
- Q : _push 는 어느 곳에서 이루어 지나?
- A : sch.__init__, sch._schedule_operation, sch.run_until->'QUEUE_REFILL'
  
## 핵심 내용 정리
- 'PHASE_HOOK' 을 통해서만 _schedule_operation 할 수 있다
- SPE.propose 는 self.now, global_state: Dict, local_state: Dict 만 참고해서 operation 을 제안한다
  - 실제로 사용하는 함수는 SPE._score(name, hook.label, global_state, local_state) 이다.
- payload 가 event 종류에 따라 다름
  - 'PHASE_HOOK' : PhaseHook: (time_use, label, die, plane)
  - 'QUEUE_REFILL' : None
  - 'OP_START' : op, die, plane
  - 'OP_END' : op, die, plane
- obligation 은 obl.on_commit(op, self.now) 으로만 만들 수 있고, obl.pop_urgent 로만 반환 받을 수 있다. _ObHeapItem class 의 attribute 에 저장됨. 후에 propose 내의 build_operation 에 의해서 Operation class 로 반환됨
- addr.precheck 는 SPE.propose 내에서 obligation 에 대해서만 이루어 진다.
- prech 는 propose 시 obligation 이 없으면 _score 로 후보 정하는 단계에서는 사용되지 않는다.
- obligation 은 source operation 의 op.targets 를 상속받아 targets 에 저장
- op.meta["source"] 의 종류는 2가지로 "obligation", "policy" 존재. 아직 op.meta["source"] 로 분기하여 처리하는 로직은 없고, 로그만 출력
- hook 은 _schedule_operation 로 operation 을 등록할 때 동일한 target die, plane 을 hook.die, hook.plane 으로 저장한다
- hook 을 다루는 것은 scheduler 와 PolicyEngine 두가지로 한정.
- propose 에서 obligation vs. policy 우선 순위를 정한다. 현재는 obligation 최우선 실행.
- operation 별로 hooks 가 정의되어 있다 : CFG->op_specs->operation->hooks
- op.states 의 dur 는 build_operation 에서 runtime 으로 생성된다.
  - dur 는 op_specs->operation->states->dist 에 분포 모양, parameter 를 정의
  - phase 별로 STATE_START, STATE_MID, STATE_END 의 hook 이 만들어짐
- global_state 는 obligation 과 policy 사이의 우선 순위 결정하는 지표 활용
- local_state 는 plane_busy_frac 에 의한 weight 만 반영
- hook 에는 time, die, plane opname: OpKind, seq.name: state name, START/MID/END 정보가 포함돼 있다. 
- addr.commit(op) 해야 erase/program 의 상태인 self.programmed 가 없데이트 된다.

# obligation 에서 _schedule_operation 까지의 workflow 정리 (완료)
- sch.run_until : 'OP_END'
- obligation 생성하여 heap 에 push
  - obl.on_commit(op, sch.now)
    - Q : 왜 sch.now 가 필요한가?
    - A : obligation 에 deadline 을 전달하기 위해서
    - self.heap 에 _ObHeapItem 의 형태로 push 됨 : heapq.heappush(obl.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._seq, ob=ob))
- scheduler 의 'PHASE_HOOK' 단계에서 obligation heappop 후 operation 생성
  - sch.run_until : 'PHASE_HOOK'
  - SPE.propose(self.now, hook, global_state, local_state)
  - ob = self.obl.pop_urgent(now_us)
    - cfg_op = self.cfg["op_specs"][ob.require.name]<br>
      op = build_operation(ob.require, cfg_op, ob.targets)<br>
      op.meta["source"]="obligation"<br>
      return op
- scheduler 에 의해 'OP_START', 'OP_END', 'PHASE_HOOK' event 생성
  - sch._schedule_operation(op, hook.die, hook.plane)
  - time 을 직접 입력하지 않고 sch.now 참조.

# propose 이해 (완료)
- obligation 이 obl.heap 에 존재할 경우, obligation 을 operation 으로 변환
  - ob = self.obl.pop_urgent(now_us)
  - self.addr.precheck(ob.require, ob.targets)
  - cfg_op = SPE.cfg["op_spec"][ob.require.name]
  - op = build_operation(ob.require, cnfg_op, ob.targets)
  - op.meta["source"]="obligation"
- obligation 이 obl.heap 에 없을 경우, score 에 따라 weight 를 부여해 operation 선택
  - s = obl._score(name, hook.label, global_state, local_state)
  - if s>0: cand.append((name, s))
  - r = random.random()*(sum(s for _,s in cand))
  - for name, s in cand: acc += s; if r<=ac: pick=name; break
  - kind = OpKind[pick]
  - targets = addr.select(kind, hook.die, hook.plane)
  - if not targets: return None
  - op = build_operation(kind, self.cfg["op_specs"][pick], targets)
  - op.meta["source"]="policy"

# select 이해 (완료)
- func signature: select(self, kind: OpKind, die:int, plane:int) -> List[Address]
- operation 종류에 따라 address 선택하는 로직 다름. return list of Address:block, page. 현재는 Address 하나만 넘기게 구현됨.
  - READ : tgt = sorted(self.programmed[(die,plane)])[0]
  - DOUT : raise RuntimeError. obligation 으로 구현
  - PROGRAM :
    - b, pgm_p, _ = self.cursors[(die,plane)] # (block,page,read_page)
    - addr = Address(die, plane, block=b, page=pgm_p)
    - pgm_p += 1
    - if pgm_p >= self.pages_per_block:
      - pgm_p = 0
      - b = (b+1) % self.blocks_per_plane
    - self.cursors[(die,plane)] = [b, pgm_p, self.cursors[(die,plane)][2]]
    - return [addr]
  - ERASE
    - b = self.block_cycle(die, plane)
      - block_cycle(die,plane) -> int: b, pgm_p, rd_p = self.cursors[(die,plane)]
    - return [Address(die, plane, block=b, page=None)]
  - return [Address(die, plane, block=0, page=0)]

# _score 이해 (완료)
- func signature : SPE._score(self, op_name: str, phase_label: str, global_state: Dict[str,str], local_state: Dict[str,str]) -> float
- SPE._core(name, hook.label, global_state, local_state)
- base weight 저장
  - w = CFG->weights->base->host.get(op_name) # {"READ":0.85, "PROGRAM": 0.10, "ERASE": 0.05, ... }
- addr.observe(die, plane) 으로 확인한 pgmable_ratio, readable_ratio, plane_busy_frac 에 해당하는 weight 값 누적곱
- addr.programmed 갯수가 많으면 pgmable_ratio weight 커지고, readable_ratio weight 는 낮아진다
  - w *= CFG->weights->g_state->pgmable_ratio.get(global_state["pgmable_ratio"]) #low/mid/high
  - w *= CFG->weights->g_state->pgmable_ratio.get(global_state["readable_ratio"]) #low/mid/high
  - w *= CFG->weights->g_local->plane_busy_frac.get(local_state["plane_busy_frac"]) #low/mid/high
- phase 내 구간에 따라 START_NEAR, MID_NEAR, END_NEAR 별로 weight 다르게 적용
- global_state 는 특정 state 에서 어떤 operation 을 선택할지 정하는 propose policy 에는 영향이 없음. obligation 과 policy 사이의 선택에 강한 bias 를 줄 수 있지만, 현재는 obligation vs. policy 사이의 우선 순위를 고려하는 로직은 구현 안돼있음.
  - near = "MID_NEAR"
  - if phase_label.endswith("START"): near="START_NEAR"
  - elif phase_label.endswith("END"): near="END_NEAR"
  - w *= SPE.cfg["weights"]["g_phase"].get(op_name, {}).get(near, 1.0) # operation 별 g_phase: {"START_NEAR":1.2, "MID_NEAR": 0.9, "END_NEAR": 1.2}
  - return w

# make_phase_hooks (완료)
- func signature: make_phase_hooks(op, start_us, cfg_op, die, plane) -> List[PhaseHook]
- hooks 조건 불러오기
  - hooks_cfg = cfg_op.get("hooks", []) # [ "when":"STATE_START", "states": ["ISSUE", ...], ...]
- hook 시작 시점 계산
  - cur = start_us + cfg_op.get("phase_offset_us", {}).get("default", 0.0)
- hook->states 와 op.states 를 비교하여 해당되는 것만 결과값에 추가
  - for seg in op.states: # seg: StateSeg : name, dur_us
    - seg_start = cur; seg_mid = cur + seg.dur_us*0.5; seg_end = cur + seg.dur_us
    - for rule in hooks_cfg: # operation 에 대한 rule: "when":"STATE_START", "states": ["ISSUE", "CORE_BUSY", "DATA_OUT"], "jitter_us": 0.1
      - if seg.name not in rule["states"]: continue #
      - jitter = rule.get("jitter_us", 0.0)
      - if rule["when"]=="STATE_START":
        - hooks.append(PhaseHook(time_us=seg_start + rand_jitter(jitter), label=f"{op.kind.name}.{seg.name}.START", die=die, plane=plane))
      - if rule["when"]=="STATE_MID":
      - if rule["when"]=="STATE_END":
    - cur = seg_end
    - return hooks

## event type 별 workflow 정리 (완료)
- 'QUEUE_REFILL'
  - queue_refill_period_us 정책에 따라 더미 'PHASE_HOOK' event push 후 다음 'QUEUE_REFILL' event push
- 'PHASE_HOOK'
  - global_state, local_state 업데이트 : observe_states(die,plane)
  - operation 제안 생성 : SPE.propose(sch.now, hook, global_state, local_state)
  - operation 스케쥴링 : _schedule_operation(op, hook.die, hook.plane)
- 'OP_START'
  - 현재는 operation 이 수행됐다는 로그만 출력
- 'OP_END'
  - operation 이 끝났다는 로그 출력
  - addr.programmed 업데이트 : addr.commit(op)
  - obligation 생성 : addr.on_commit(op, self.now)

## 보완 사항 (우선 순위 별 오름차순 정렬됨)
- BusySlot 은 왜 만들었는지 확인 필요
- observe_states 에서 특정 die, plane 에 대해서 ratio 구하는 방식이 맞는지 검토 필요
- CFG->op_specs->hooks->states 는 phase 별로 적용할 것을 명시하고 있으나, 일괄적으로 phase 당 3개로 고요해서 단순하게 정리 필요
- addr.observe_states(hook.die, hook.plane) 에서 시간 정보 추가 필요. pgmable_ration, readalbe_ratio, local_plane_frac 등도 현재 시간에 맞춰 업데이트 돼야 한다.
- addr.select(kind, die, plane) 에서 multi-plane operation 시 List[Address] 형태로 반환 필요
- precheck 함수 구현 필요
- obligation 으로 만들어진 operation 은 종류에 따라서 op.targets 에서 연산하는 로직 구현 필요. sequential read/pgm 는 page address + 1 , cache read 의 경우에 순서상 cache read 가 먼저 나오고 dout 이 나중인데, dout 의 address 는 cache read 의 address -1 이다.
- _schedule_operation -> addr.reserve(die,plane,start,end) 시에 operation 에 따라서 erase/pgm/multi-plane operation 의 경우 해당되는 plane 모두 reserve 처리 필요
- policy operation 의 선택은 weight 보다는 정해진 operation.phase 조건 하의 operation 확률 weight 값을 받아와서 random choice 해야함.
- policy 와 obligation 의 우선 순위를 처리하는 로직 구현 필요. 현재는 obl.pop_urget(sch.now) 는 구현없이 조건 obl.heappop 을 하게 돼있음
- op.movable 이 정말 필요한지 검토
- 두 개 이상의 operation 을 순차적으로 예약하는 경우 처리 로직 어느 단계에서 구현할 지 검토. addr.precheck 만으로 충분한지 관점
  - sequential read/program 이 예약된 경우, 동일 block 상에 erase/program/read 는 금지해야 한다.
  - obligation 이 heap 에 여러개 쌓여 있을 경우, 어느 obligation 부터 꺼낼지 로직 구현 필요. 현재는 deadline 기준으로 정렬이 되고, obligation 사이에 dependency check 필요한지 여부 검토 필요
- make_phase_hooks 으로 'PHASE_HOOK' event push 할 떄 pattern time resolution 고려 time sampling 필요
- plane interleave read 를 만들기 위해서, 스케쥴러가 개입을 하는 것이 필요한지 검토
- obligation, policy operation 간에 addr.select 를 쓰고 안쓰고 정해지는데, 통합해야 할 필요성? obligation 은 source  operation 의 targets 상속받는 형식이지만, addr.select 는 샘플링 하는 방식
- make_phase_hooks(op, start, sch.cfg["op_specs"][op.kind.name], die, plane) 에서 op 를 넘겨줌에도 별도로 sch.cfg["op_specs"][op.kind.name] 을 넘겨주고 있다. 통합 가능한지 검토
- sch._schedule_operation 에서 dur 계산 로직 수정 필요. StateSeq, Operation clss 구현과 연결됨. Stateseq.times 는 절대값을 저장하므로 마지막 값만 추출해서 사용가능. 하지만, 현재 구현상 times 에 저장되는 값은 state 의 끝나는 시간이 저장됨. 그리고 operation 이 끝난 후는 state 가 다른 operation 이 수행되기 전까지 무한히 유지되므로 times 의 마지막 값은 10ms 의 매우 큰 값이 저장돼 있음 
  - @dataclass<br>
  class StateSeq<br>
  times: List[float]<br>
  states: List[str]<br>
  id: int<br>