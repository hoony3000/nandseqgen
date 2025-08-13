# nandsim_demo.py 나만의 질문과 이해

## 질문 및 답변
- Q : available_at 은 언제 업데이트 되나?
- A : ...
- Q : propose 할 때 모든 context check 다 하나?
- A : self.now, global_state, local_state 만 참고해서 제안
- Q : global_state, local_state 는 언제 업데이트 되는가?
- A : 'PHASE_HOOK' event 처리 시 맨 첫번째로 업데이트 후 propose 실행. 이후 
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

# 정리 필요한 부분
- hooks: List[PhaseHook] = make_phase_hooks(op, start, self.cfg["op_specs"][op.kind.name], die, plane)
- op = self.SPE.propose(self.now, hook, global_state, local_state)
- BusySlot 은 왜 만들었나?
- addr.commit(op) : OP_END 시 addr.programmed 에 Erase/PGM 상태 기록
- ObligationManager 함수
  - self.specs : CFG["obligations"]
  - self.heap : List[_ObHeapItem]
  - self._seq : self.heap 에 push 할 때마다 +1
  - addr.on_commit(op, self.now)
- _ObHeapItem dataclass
- build_operation : propose 함수 안에 존재
- AddressManager
  - addr.select(kind, hook.die, hook.plane) : SPE.propose 안에 존재
  - precheck : SPE.propose 안에 obligation 처리 과정 중
- PolicyEngine
  - self.cfg = cfg
  - self.addr = addr_mgr
  - self.obl = obl_mgr
  - **_score**
  
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

# obligation 에서 _schedule_operation 까지의 workflow 정리
- sch.run_until : 'OP_END'
- obl.on_commit(op, sch.now)
  - Q : 왜 self.now 가 필요한가?
  - A : ...
- self.heap 에 _ObHeapItem 의 형태로 push 됨 : heapq.heappush(obl.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._seq, ob=ob))
- sch.run_until : 'PHASE_HOOK'
- SPE.propose(self.now, hook, global_state, local_state)
- ob = self.obl.pop_urgent(now_us)
  - cfg_op = self.cfg["op_specs"][ob.require.name]<br>
    op = build_operation(ob.require, cfg_op, ob.targets)<br>
    op.meta["source"]="obligation"<br>
    return op
- sch._schedule_operation(op, hook.die, hook.plane)

# propose 이해
- ob = self.obl.pop_urgent(now_us)
- self.addr.precheck(ob.require, ob.targets)

## event type 별 workflow 정리
- 'QUEUE_REFILL'
  - queue_refill_period_us 정책에 따라 더미 'PHASE_HOOK' event push 후 다음 'QUEUE_REFILL' event push
- 'PHASE_HOOK'
  - global_state, local_state 업데이트 : observe_states(die,plane)
  - operation 제안 생성 : SPE.propose(self.now, hook, global_state, local_state)
  - operation 스케쥴링 : _schedule_operation(op, hook.die, hook.plane)
- 'OP_START'
  - 현재는 operation 이 수행됐다는 로그만 출력
- 'OP_END'
  - operation 이 끝났다는 로그 출력
  - addr.programmed 업데이트 : addr.commit(op)
  - obligation 생성 : addr.on_commit(op, self.now)

## 보완 사항
- addr.observe_states(hook.die, hook.plane) 에서 시간 정보 추가 필요 > addr.observe_states(hook.die, hook.plane, sch.now)
  - local_state 에 nand_state 포함되어야 한다. 예를 들면, erase_busy, pgm_busy 등. nand_state 에 따라 금지/허용 operation 이 나뉘기 때문에 핵심적인 요소
  - plane_busy_frac 의미를 명확히 하고, 값을 반환하는 로직 구현 필요.
- make_phase_hooks 으로 'PHASE_HOOK' event push 할 떄 pattern time resolution 고려 time sampling 필요
- sch._schedule_operation 에서 dur 계산 로직 수정 필요. StateSeq, Operation clss 구현과 연결됨.Stateseq.times 는 절대값을 저장하므로 마지막 값만 추출해서 사용가능. 하지만, 현재 구현상 times 에 저장되는 값은 state 의 끝나는 시간이 저장됨. 그리고 operation 이 끝난 후는 state 가 다른 operation 이 수행되기 전까지 무한히 유지되므로 times 의 마지막 값은 10ms 의 매우 큰 값이 저장돼 있음 
  - @dataclass<br>
  class StateSeq<br>
  times: List[float]<br>
  states: List[str]<br>
  id: int<br>
- 두 개 이상의 operation 을 순차적으로 예약하는 경우 처리 로직 어느 단계에서 구현할 지 검토. addr.precheck 만으로 충분한지 관점
  - sequential read/program 등. 이 때 동일 block 상에 read/program 은 금지해야 한다.
  - obligation 이 순차적으로 예약된 경우 
- plane interleave read 를 만들기 위해서, 스케쥴러가 개입을 하는 것이 필요한지 검토
- precheck 함수 구현
- priority 핸들링
- payload 는 