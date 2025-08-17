## nandsim_demo.py

### 목적
고수준 정책 엔진과 스케줄러가 난수 기반 시뮬레이션으로 NAND 연산(READ/PROGRAM/ERASE/SR/DOUT)을 생성·검증·기록하는 흐름을 정의한다. 핵심 구성요소는 `Scheduler`, `PolicyEngine`, `AddressManager`, `ExclusionManager`, `LatchManager`, `ObligationManager` 이며, 타임라인은 `TimelineLogger` 로 기록된다.

---

## 1) Workflow: operation 하나가 추가되기까지

- **이벤트 루프 구동**: `Scheduler` 는 0us에 `QUEUE_REFILL` 과 각 plane별 `PHASE_HOOK(BOOT.START)` 이벤트를 큐에 적재한다.
- **PHASE_HOOK 처리 → propose 호출**:
  - 각 훅 시점마다 현재 `now_us` 기준 관측(`AddressManager.observe_states`)과 plane 가용시간을 바탕으로 `earliest_start` 를 계산한다.
  - `PolicyEngine.propose(now_us, hook, g, l, earliest_start)` 로 다음 operation 후보를 질의한다.
- **후보 계획 수립**: `PolicyEngine` 가 선택한 kind와 arity에 따라 `AddressManager.plan_multipane` 이 타깃 주소/plane set과 `Scope` 를 산출한다. 이어 `build_operation` 으로 `Operation` 을 구성한다.
- **스케줄 직전 최종 점검 및 예약**: `Scheduler._schedule_operation` 에서 시작/종료 시각을 정하고 다음을 순차 점검한다.
  - `bus_precheck` → `latch.allowed` → `excl.allowed` (fail-safe)
  - 통과 시 plane scope 예약(`reserve_planescope`), 버스 예약(`bus_reserve`), 배제창 등록(`ExclusionManager.register`), 미래상태 갱신(`register_future`), READ의 경우 래치 잠금 계획(`LatchManager.plan_lock_after_read`)을 수행한다.
- **훅 재적재 및 실행 로그**: 각 state 구간의 START/MID/END 훅을 재적재하고, `TimelineLogger` 에 기록한다. `OP_START`/`OP_END` 이벤트 처리 시 커밋/의무 갱신이 진행된다.

---

## 2) Obligation 생성부터 operation 전환까지

- **생성 시점 (`ObligationManager.on_commit`)**:
  - `READ` 완료 시 `CFG["obligations"]` 정의에 따라 `DOUT` 의무를 생성한다.
  - 마감시각(`deadline_us`)은 윈도우 분포로 산출되며, 최소-마감 기반 우선순위 힙으로 관리된다.
- **선택 시점 (`ObligationManager.pop_urgent`)**:
  - propose 단계의 최우선으로 동일 die/plane, horizon 내, `earliest_start ≤ deadline` 조건을 만족하는 의무를 팝한다. `hard_slot` 인 경우 horizon을 무시한다.
- **operation 화**:
  - 의무가 선택되면 해당 타깃으로 `build_operation` 하여 `DOUT` operation 후보를 만들고, 아래 레이어 점검을 통과하면 스케줄된다.
- **상태 전이**:
  - 스케줄 승인 시 `mark_assigned`, 완료 시 `mark_fulfilled(now)` 로 성공/기한내 달성 여부가 집계된다. 마감 경과 시 `expire_due(now)` 로 만료 처리된다.

---

## 3) propose 단계의 우선순위와 레이어(게이트) 흐름

`PolicyEngine.propose` 는 의무 → phase-conditional 두 단계로 후보를 시도하며, 각 단계에서 동일한 안전 게이트를 통과해야 한다. (Backoff는 충돌 정합성 해소 정책에 따라 제거)

- **우선순위 단계**:
  1) obligation: `ObligationManager.pop_urgent` 로 의무 기반 후보를 최우선 검토
  2) phase-conditional: 훅 컨텍스트별 분포(`get_phase_dist`)로 alias 포함 후보 선택, 필요 시 arity 강등(degrade)
  3) score backoff: 제거됨

- **각 단계 공통 게이트 순서**:
  - `start_hint` 산정: `AddressManager.candidate_start_for_scope(now, die, scope, plane_set)`
  - `_admission_ok(now, hook_label, kind, start_hint, deadline?)`
    - near-future 게이팅. 의무는 기본적으로 `obligation_bypass=True` 이면 우회. 비의무는 phase/op별 Δ에 따라 허용.
  - `precheck_planescope(kind, targets, start_hint, scope)`
    - plane 시간 중복, 주소 일관성, READ 커밋 유효성, PROGRAM 다음 페이지 제약 등 사전검사.
  - `bus_precheck(start_hint, bus_segments_for_op(op))`
    - 전역 버스 예약과 충돌 여부.
  - `LatchManager.allowed(op, start_hint)`
    - READ 완료~DOUT 완료까지 같은 (die, plane) 에 대한 래치 보존. READ/PROGRAM/ERASE 가 래치 구간과 겹치면 거부. DOUT/SR 은 허용.
  - `_exclusion_ok(op, start_hint)` ≡ `ExclusionManager.allowed`
    - `CFG.constraints.exclusions` 에 따른 글로벌/다이 배제창과의 충돌 필터. PROGRAM/ERASE/MUL_READ CORE_BUSY 동안 READ/PROGRAM/ERASE 차단, DOUT 전체 기간 글로벌 동결 등.

- **승인 시 메타**: `op.meta` 에 `source`, `phase_key_used`, `alias_used`, `arity`, `scope`, `plane_list`, 필요 시 `obligation` 을 설정한다.

---

## 4) Obligation을 반드시 operation으로 스케줄하기 위한 방법들

- **우선순위 보장**: propose의 1순위가 의무 처리다. 다른 모든 선택(phase-conditional, backoff)보다 먼저 시도한다.
- **Admission 우회**: `CFG["admission"]["obligation_bypass"] = True` 인 경우 `_admission_ok` 검사를 건너뛴다. 기한 임박 의무가 near-future 게이트에 막히지 않도록 한다.
- **Hard slot**: 의무 스펙의 `priority_boost.hard_slot=True` 이면 `pop_urgent` 시 horizon 제한을 무시한다. (기본 horizon_us=10.0)
- **반복 시도**: 한 번 거절돼도 힙에 남아 다음 훅에서 재시도된다. 마감 전까지 반복적으로 최우선 평가된다.
- **래치/배제/버스 충돌 최소화**: DOUT은 래치 허용(`LatchManager.allowed=True`), 스펙상 `DOUT`은 전역 배제창을 생성해(스케줄 수락 후) 이후 충돌을 줄인다. 다만 스케줄 직전 게이트(버스/배제)는 여전히 통과해야 한다.
- **실무 팁(CFG)**: DOUT 윈도우를 넉넉히(`window_us` 확대) 또는 `hard_slot` 활성화, obligation 우회 유지, READ→DOUT 경로에서 버스 구간이 겹치지 않도록 DOUT의 `states` 조정으로 성공률을 높일 수 있다.
 - **MUL_READ 이후 DOUT 분산 원칙**: 멀티플레인 READ가 끝난 뒤 생성되는 DOUT 의무는 plane_set 오름차순으로 deadline을 분산(stagger)하여 순차 실행한다. 각 DOUT은 글로벌 배제창(ANY)을 생성하므로, deadline을 시간적으로 분리해 상호 겹치지 않도록 배치해 전역 배제를 위반하지 않게 진행한다.

---

## 5) Phase-conditional 값으로 operation을 스케줄에 등록하는 과정

1) 훅별 분포 조회: `get_phase_dist(hook.label)` 로 분포와 사용 키를 얻는다.
2) 룰렛 선택: `roulette_pick(dist, allow)` 로 alias 포함 후보를 하나 선택한다.
3) Alias 해석: `resolve_alias` 로 base kind와 fanout 조건(eq/ge)을 해석한다.
4) Fanout/Interleave 결정: `_fanout_from_alias` 가 phase override(`selection.phase_overrides`)와 alias 제약을 결합한다.
5) 타깃 계획: `AddressManager.plan_multipane(kind, die, hook_plane, fanout, interleave)` 로 `targets, plane_set, scope` 산출.
   - 실패 시 arity 강등(degrade) 1회 시도(fanout>1 → 1) 후 재시도.
6) `start_hint` 계산 후 게이트 통과: `_admission_ok` → `precheck_planescope` → `bus_precheck` → `latch.allowed` → `_exclusion_ok`.
7) 승인 시 `op.meta` 설정(`source=policy.phase_conditional`, `phase_key_used=...`, `alias_used=...`) 후 스케줄러에 전달.

---

## 6) Backoff (제거)

- 충돌 정합성 해소 계획에 따라 backoff 단계를 제거했다. 후보 부족 상황은 phase-conditional 재설계/부트스트랩/사전 스크리닝으로 대응한다.

---

## 7) AddressManager

- **Reserve → Commit 과정**:
  - Reserve: `reserve_planescope(op, start, end)` 가 해당 scope의 plane 가용시각(`available`)을 갱신하고 예약 목록(`resv`)에 추가.
  - Bus 예약: `bus_reserve(start, bus_segments_for_op(op))` 로 전역 버스 점유 구간 등록.
  - 미래상태 갱신: `register_future(op, start, end)` 가 PROGRAM의 next page 반영, ERASE 시 미래 상태를 -1로 갱신하고 write head 조정.
  - Commit: `commit(op)` 이 커밋 상태를 최종 갱신(READ은 커밋 검증만, PROGRAM은 `programmed_committed` 추가, ERASE는 해당 블록의 기록 제거).

- **Address assign (multi-plane 포함)**: `plan_multipane`
  - READ: 모든 선택 plane에서 공통으로 읽을 수 있는 페이지 교집합을 구해 동일 page를 타깃팅.
  - PROGRAM: 각 plane의 write head 기준 다음 페이지들을 집계, 최빈값 페이지 또는 초기화(0) 후보를 선택한 뒤 plane별 유효 블록을 매핑.
  - ERASE: 각 plane에서 비소거 블록을 우선 선택, 없으면 write head 블록 사용.
  - SR: 단일 plane에 균일 주소 형식으로 배정.

- **Bus control**:
  - 세그먼트 추출: `bus_segments_for_op(op)` 가 state의 `bus=True` 구간을 상대 오프셋으로 산출.
  - 충돌 확인: `bus_precheck(start_hint, segs)` 가 전역 `bus_resv` 와 겹침 여부를 검증.
  - 예약 확정: 스케줄 승인 시 `bus_reserve` 로 실제 점유 구간 저장.

- **Latch control 연계**:
  - 래치 판정은 `LatchManager.allowed` 가 담당. AddressManager는 READ 스케줄 승인 후에야 `LatchManager.plan_lock_after_read` 가 호출되도록 흐름을 제공한다.

- **Exclusion control 연계**:
  - 배제창 판정/등록은 `ExclusionManager` 가 담당. AddressManager는 scope/타깃 제공과 예약 타이밍을 통해 `ExclusionManager.register` 가 정확한 시간창을 생성하도록 연계한다.

---

## 8) earliest_start 계산 로직

- 정의: `AddressManager.earliest_start_for_scope(die, scope, plane_set)`
  - `DIE_WIDE`: 해당 die의 모든 plane 가용시각의 최대값
  - `PLANE_SET`: 지정된 plane 집합의 가용시각 최대값
  - 기타: 단일 plane의 가용시각
  - 가용시각은 `reserve_planescope` 시 갱신되며, 동일 scope 내 중복 점유를 방지한다.

---

## 9) start_hint 계산 로직

- 정의: `AddressManager.candidate_start_for_scope(now_us, die, scope, plane_set)`
  - `start_hint = quantize(max(now_us, earliest_start_for_scope(...)))`
  - propose 단계에서 게이트 입력으로 사용되며, 실제 스케줄 시에도 `Scheduler._start_time_for_op` 가 동일 원칙으로 재계산하여 fail-safe를 보장한다.

---

## 10) phase_override의 사용 시점, 효과

- **사용 시점**:
  - alias 경유 후보에서 fanout 계산 시: `PolicyEngine._fanout_from_alias` → `get_phase_selection_override(hook_label, base_kind)`
  - backoff 후보에서 fanout 계산 시: `get_phase_selection_override(hook_label, pick)`
- **키 해석 순서**: `OP.STATE.POS` → `OP.STATE` 순으로 탐색, 없으면 kind별 `selection.defaults` 적용.
- **효과**:
  - `fanout`과 `interleave`를 훅 컨텍스트별로 강제/상향하여 병렬도와 배치 방식을 제어.
  - 예: `READ.CORE_BUSY.START: {fanout: 4, interleave: True}` → 멀티플레인 READ 시도를 촉진. 실패 시 alias degrade 경로로 단일화 재시도.

---

## 11) plan_multipane 동작 과정

- **입력**: `(kind, die, start_plane, desired_fanout, interleave)`
- **팬아웃 강등 루프**: `f = desired_fanout..1` 순으로 시도.
- **plane-set 후보 생성**: `_random_plane_sets(f, tries, start_plane)`
  - 무작위 샘플로 후보 생성, 60% 확률로 `start_plane` 포함 보정, 중복 제거.
- **kind별 타깃 산출**:
  - READ: 각 plane의 커밋 페이지 교집합을 구해 공통 `page` 선택(무작위 1개). 각 plane에서 해당 page를 가진 블록을 매핑. `Scope.PLANE_SET` 반환.
  - PROGRAM: 각 plane의 write head로부터 다음 페이지들을 집계해 최빈값 page 후보 산출. 필요 시 `0`(초기화)도 후보. 각 plane에서 해당 page를 만들 수 있는 블록을 선택(현재 head 또는 탐색). `Scope.DIE_WIDE` 반환.
  - ERASE: 각 plane에서 비소거 블록을 우선 선정, 없으면 write head 사용. `Scope.DIE_WIDE` 반환.
  - SR: 단일 plane 대상으로 균일 주소 형식 반환. `Scope.NONE`.
- 실패 시 다음 후보 plane-set 또는 더 낮은 fanout으로 재시도, 전부 실패하면 `None`.

---

## 12) Latch control 동작 과정

- **락 설정**: READ 스케줄 승인 시 `LatchManager.plan_lock_after_read(targets, read_end_us)` → (die, plane)에 열린 락 등록(end=None).
- **허용 판정**: `LatchManager.allowed(op, start_hint)`
  - DOUT/SR은 항상 허용.
  - `DIE_WIDE` 연산: 동일 die 내 임의 plane이 락 활성 구간이면 거부.
  - `PLANE_SET`/단일: 타깃 plane 중 락 활성 구간이 있으면 거부.
- **락 해제**: DOUT 완료 이벤트에서 `release_on_dout_end(targets, now)` 로 해당 plane 락 제거.
- 결과적으로 READ→DOUT 사이 데이터 래치 보존을 보장하며, 충돌하는 READ/PROGRAM/ERASE를 차단.

---

## 13) Operation × Address dependency rule

- **사전검사(`precheck_planescope`) 규칙**:
  - plane 일관성: `t.plane == t.block % planes` 요구.
  - PROGRAM: `t.page == future_last + 1` 이고 `t.page < pages_per_block` 여야 함.
  - READ: `(block, page)` 가 `programmed_committed` 에 존재해야 함(기 커밋 데이터).
  - ERASE: 별도 페이지 제약 없음.
- **상태 전이**:
  - 미래 상태(`register_future`):
    - PROGRAM: `addr_state_future[(die, block)] = page` 갱신. 블록이 가득 차면 해당 plane의 첫 소거 블록으로 write head 이동.
    - ERASE: `addr_state_future = -1` 로 재설정, write head 를 해당 블록으로 복귀.
  - 커밋(`commit`):
    - PROGRAM: `addr_state_committed` 갱신 후 `(block, page)` 를 `programmed_committed` 에 추가.
    - ERASE: `addr_state_committed = -1`, 해당 블록의 커밋 페이지들을 제거.
- **룰 위반**: 위 제약 미충족 시 `precheck_planescope` 에서 거부되어 스케줄되지 않는다.

---

## 14) AddressManager의 address 샘플링과 다양성 확보

- **plane-set 다양화**: `_random_plane_sets` 가 무작위 표본과 `start_plane` 포함 보정을 결합, `planner_max_tries` 회 재시도로 후보군 확대.
- **페이지 선택 다양화**:
  - READ: 교집합에서 무작위 페이지 1개 선택.
  - PROGRAM: 각 plane의 다음 페이지 분포의 최빈값을 사용하되, 초기화 가능 시 `0`도 후보에 포함해 경로 분산.
- **재현성**: `CFG["rng_seed"]` 로 난수 초기화. 동일 시드에서 동일 시퀀스 재현 가능.

---

## 15) global_state, local_state 의 계산 과정: die, plane 레벨

- **계산 함수**: `AddressManager.observe_states(die, plane, now_us) -> (g, l)`
- **global_state(g_state) 계산(plane 레벨)**:
  - 대상: 지정 `plane` 내 모든 블록 반복(`iter_blocks_of_plane`).
  - `pgmable_blocks`: 미래 페이지가 마지막-1 미만(=추가 PROGRAM 가능)인 블록 수.
  - `readable_blocks`: 커밋 페이지가 0 이상(=읽을 데이터 존재)인 블록 수.
  - 각 비율을 `low/mid/high` 버킷으로 변환 → `{"pgmable_ratio": ..., "readable_ratio": ..., "cls": "host"}` 반환.
- **local_state(g_local) 계산(plane 레벨)**:
  - `plane_busy_frac`: `available_at(die, plane) > now_us` 이면 `high`, 아니면 `low`.
  - 즉, 해당 plane의 즉시 가용 여부를 이산화한 현지 혼잡도 지표.
- **사용 레벨**:
  - `Scheduler` 가 훅마다 `observe_states` 호출 → `PolicyEngine.propose` 전달.
  - `PolicyEngine._score` 에서 가중치 적용: `weights.g_state.*`(pgmable/readable) × `weights.g_local.plane_busy_frac.*`.
- **die 차원**:
  - 인터페이스는 die/plane 인자를 받지만, 현재 이벤트/스케줄은 단일 die(0)에서 생성된다. 따라서 계산은 실질적으로 die 0의 각 plane 레벨에서 수행된다(설계상 다중 die 확장 가능).

---

## 16) pop_urgent에서 obligation 선택 로직

- 입력/우선순위:
  - 입력: `now_us`, `die`, `plane`, `horizon_us`, `earliest_start`
  - 힙 정렬: `deadline_us` 1순위, 동일 시 `seq` 2순위(`_ObHeapItem(deadline_us, seq, ob)`)

- 선택 조건(AND):
  - `same_die`: `ob.targets[0].die == die`
  - `same_plane`: `plane ∈ {a.plane for a in ob.targets}`
  - `in_horizon`: `(ob.deadline_us - now_us) ≤ max(horizon_us, 0)` 또는 `hard_slot == True`
  - `feasible`: `earliest_start ≤ ob.deadline_us`

- 절차:
  1) 현재 힙의 최상단(가장 이른 `deadline_us`)부터 항목을 꺼내 위 조건을 검사
  2) 조건 만족 시 해당 `obligation`을 선택(CHOOSE)하고 루프 종료
  3) 조건 불만족 시 임시 목록(kept)에 보관(SKIP) 후 다음 항목 검사
  4) 루프 종료 후 kept 항목 전부를 다시 힙에 되돌려 push
  5) 반환값: 선택된 `obligation`(없으면 `None`)

- 시간/정밀도 처리:
  - 비교 전 `now_us`, `earliest_start`는 시뮬레이션 해상도로 `quantize` 되어 비교된다

- 생성 시점 특성(멀티플레인 READ→DOUT):
  - 멀티플레인 READ 완료 시, 각 plane별 DOUT 의무는 `plane_stagger_us` 만큼 `deadline_us`를 계단식으로 늘려 생성되므로, 힙에서 plane 오름차순으로 자연스러운 순차 선택이 유도된다

- 선택 이후:
  - 스케줄 승인 시 `mark_assigned(ob)` → 완료 시 `mark_fulfilled(ob, now)` 로 통계가 집계된다(힙에서는 선택 시점에 제거됨)

---

## 17) none_available 발생 경로(의미와 원인)

none_available은 유효성 게이트(precheck/bus/latch/excl) 실패가 아니라, “해당 훅 시점에 선택할 후보가 없음”을 의미한다. 따라서 계획(populate)과 유효성(validity)이 옳더라도, 훅 타이밍/plane 매칭/가시 창(horizon) 등의 이유로 정상적으로 발생할 수 있다.

- 의무 단계(`stage = obligation`)에서의 원인:
  - 힙 비어 있음: `ObligationManager.heap`에 의무가 없음
  - plane 불일치: 현재 훅 `hook_plane`이 `ob.targets`의 plane 집합에 포함되지 않아 `same_plane=False`
  - 시간창 미충족: `(deadline_us - now_us) > horizon_us`이고 `hard_slot=False` → `in_horizon=False`
  - 실행 불가능: `earliest_start > deadline_us` → `feasible=False`
  - (다중 die 환경) die 불일치: `same_die=False`

- 정책 단계(`stage = phase_conditional`)에서의 원인:
  - 분포 없음: `get_phase_dist(hook.label)`이 훅에 대한 분포를 찾지 못함
  - 가중 0: 필터링 이후 전체 가중치가 0이 되어 `roulette_pick`이 후보를 뽑지 못함
  - 타깃 없음: `plan_multipane`이 타깃을 만들지 못함
    - READ: 각 plane의 커밋 페이지 교집합이 비어 있음
    - PROGRAM: 선택 page에 대해 일부 plane에서 가능한 블록을 찾지 못함(최빈값/0 후보 모두 실패)
    - ERASE: 비소거 블록 찾기 실패(이론상 드뭄), 또는 내부 정책 상 다른 이유로 None
    - alias 강등(degrade) 후에도 단일화 실패

참고:
- 위 원인들은 유효성 게이트(`precheck_planescope`/`bus_precheck`/`LatchManager.allowed`/`_exclusion_ok`) 이전 단계에서 발생하며, 해당 게이트 실패 시에는 각각 `precheck`/`bus`/`latch`/`excl`로 사유가 기록된다(§3 참조).
- 부트스트랩이 멀티플레인 체인으로 계획되더라도, 훅은 plane 단위로 도착하므로 다른 plane에 속한 의무는 그 훅에서는 “없음”(none_available)으로 기록될 수 있다. 이는 정상 동작이며, 매칭되는 plane의 훅에서 소비된다.

---

## 18) Bootstrap

- 목적
  - 초기 상태에서 READ/PROGRAM/ERASE/DOUT 경로를 강제 워밍업해 의무 생성·소모 경로를 검증
  - Exit 이후 정책(phase-conditional)으로 자연스럽게 전환되도록 안전한 backlog 구성

- 생성 과정
  - 함수: `populate_bootstrap_obligations(cfg, addr, obl)`
  - 페이지 수: `k = floor(pages_per_block × clamp(pgm_ratio, 0..max_ratio))`
  - 스트라이프 순회: 모든 plane을 한 묶음으로 하는 block stripes 단위 순서대로 진행
  - p=0에서 ERASE(die-wide) 생성 → 이후 p마다 PROGRAM(die-wide) → READ(multi-plane) → DOUT(plane별) 의무 생성
  - 마감 산정: `deadline_window_us` + `stage_gap_us`/`stagger_us` + 의존성 여유(eps: PROGRAM 2.0us, READ 2.0us, order 0.2us 상수화)
  - DOUT 분산: `dout_stagger_n`(× DOUT_nominal) 또는 `stagger_us` 중 큰 값으로 plane별 deadline 계단화
  - 메타: `source="bootstrap"`, READ 의무는 `skip_dout_creation=True`로 on_commit 중복 방지
  - 로깅: 생성 이벤트는 `obligation_creations.csv`로 저장(Stripe/Page/Plane 등 포함)

- 실행 옵션들
  - CFG.bootstrap
    - `enabled: bool`(on/off)
    - `pgm_ratio: float`, `max_ratio: float`
    - `deadline_window_us: float`, `stage_gap_us: float`, `stagger_us: float`
    - `hard_slot: bool`(의무 하드 슬롯)
    - `dout_stagger_n: float`(0이면 미사용)
    - `disable_timeline_logging: bool`(부트스트랩 동안 타임라인 로깅 비활성)
    - `split_timeline_logging: bool` + `bootstrap_timeline_path`, `policy_timeline_path`(분리 저장)
  - 러닝타임 산식(코드 반영):
    - `run_until_base = policy.run_until_us`
    - `run_until_bootstrap = quantize(last_deadline_boot + num_bootstrap_obligations × policy.run_until_bootstrap_margin_per_ob_us)`
    - `run_until_tot = run_until_base + run_until_bootstrap`
    - 기본 `policy.run_until_bootstrap_margin_per_ob_us = 3.0`
  - 정책 가드: 부트스트랩 의무가 남아 있는 동안 `PolicyEngine.propose`는 정책 제안을 스킵(안전 전환)

- pitballs
  - 러닝타임 부족: `run_until_us`만 사용하면 drain 전 종료될 수 있음 → 위 산식으로 `run_until_tot` 보장
  - 충돌 밀집: DOUT/READ가 빽빽하면 `DOUT_OVERLAP` 검출 증가 → `dout_stagger_n`/`stagger_us`/`stage_gap_us`로 완화
  - 과도한 `pgm_ratio`: 힙/CSV가 매우 커지고 실행시간 증가 → `max_ratio`로 상한, 단계적 실험 권장
  - Admission 간섭: 의무는 `obligation_bypass=True` 유지(near-future 게이트로 막히지 않도록)
  - 로깅/시각화: `disable_timeline_logging=True`면 단일 df가 비어 플롯 오류 가능 → `split_timeline_logging=True`로 부트스트랩/정책 분리 저장·시각화 권장
  - 제약 구성: DOUT 전역 배제창 제거 시 버스/래치만으로도 충분하나, 워크로드에 따라 `soft_defer/bus` 증가 가능 → 스태거/간격 조정 필요