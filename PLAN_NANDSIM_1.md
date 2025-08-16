### PLAN_NANDSIM_1

#### 목적/의도
- 정책/스케줄 파이프라인의 확률/샘플링/우선순위/의무(Obligation) 보장을 명확히 하여, 의도치 않은 기아를 줄이고 제어 가능성을 높인다.
- Address/Phase/Latch/Exclusion 상호작용을 설계적으로 정리하고, 실행 전 정합성 검사를 강화한다.

---

## 1) phase_conditional 확률 합 체크 (CFG 사전검사)
- **의도**: 훅 분포의 합이 1이 아닐 경우 불안정한 샘플링을 사전에 차단.
- **변경**: `Scheduler` 구동 직전 또는 `main()` 초기화 시, `CFG["phase_conditional"]`의 각 키에 대해 확률 합≈1.0 검사. 불일치 시 오류/중단.
- **수정 포인트**: `main()` 초기화 블록.

## 2) 샘플링 로직 개선 (numpy)
- **의도**: 일관된 가중치 추출 및 테스트 재현성 향상.
- **변경**:
  - `roulette_pick` → `np.random.choice` 기반으로 대체.
- **수정 포인트**: `roulette_pick`. `import numpy as np` 추가 및 RNG 시드 연동.

## 3) admission→phase_overrides 관련 코드 삭제
- **의도**: admission 게이트는 phase별 델타만 사용하고, 별도 overrides 경로 제거로 단순화.
- **변경**: `_admission_ok`, `get_admission_delta`에서 phase_overrides 의존 제거.
- **수정 포인트**: `PolicyEngine._admission_ok`, `get_admission_delta`, `CFG.admission` 스키마 간소화.

## 4) phase_conditional 에서 OP.PHASE.POS 형태 삭제
- **의도**: 훅 키 체계를 단순화하여 관리 비용 감소.
- **변경**: `get_phase_dist`의 키 탐색을 `OP.PHASE` → `DEFAULT` 만 지원. `OP.PHASE.POS` 제거.
- **수정 포인트**: `get_phase_dist`, `CFG.phase_conditional` 키 형태 정리.

## 5) LatchManager.allowed 조건 정리
- **의도**: 래치 보존 정책을 명확화. Operation 의 확장성을 위한 조건 변경
- **변경**: 조건문을 "ERASE/PROGRAM/READ vs 그 외" 두 갈래로 단순화.
- **수정 포인트**: `LatchManager.allowed` 분기 재구성.

## 6) Obligation deadline 산정 개선
- **의도**: 현실적인 deadline 설정(현재 예약 끝단 이후를 기준으로)로 이행성/우선순위 안정화.
- **변경**: `ObligationManager.on_commit` 호출 전, 동일 타깃 plane들의 예약 끝시각(max reserved end)을 기준으로 `deadline = end + window` 산정.
- **수정 포인트**: `Scheduler.OP_END` → `obl.on_commit` 경로 또는 `ObligationManager.on_commit` 내부에서 address/busy 조회.
## 7) Obligation 100% 스케줄 및 기아 완화
- **의도**: 의무 소비 보장과 정책 기아 방지의 균형.
- **정책**:
  1) Heap 규모 커짐 허용(bootstrapping/host 유발). 성능 최적화는 별도 고려.
  2) Obligation의 `targets` 는 push 시점에 고정(미리 확정)하여 의존성 충돌 최소화.
  3) Heap 순서는 변경 불가(MUST). 우선순위는 deadline 기반 유지.
  4) 점유시간 트래킹: 일정 기간(`obl_release_period`) 동안 의무만 소비 후, 전 래치 해제 시점까지 의무만 허용. 이후 `obl_lock_period` 동안 의무 propose 금지(Policy만). 그 기간 heap 내 모든 항목의 `deadline += obl_lock_period` 로 연장. 단, 부트스트랩 유발 의무는 예외적 최우선.
  5) Latch control 로 READ→DOUT 순서를 보장.
- **수정 포인트**: `ObligationManager`(통계/윈도 관리), `PolicyEngine.propose`(토글 기간 준수), `LatchManager`(기존 유지).

## 8) Propose 선택율 향상 방안
- **의도**: 초기/중간 상태에서 후보 부족/충돌로 인한 정체 방지.
- **정책**:
  1) Bootstrapping: `pages_per_block * bootstrap_pgm_ratio` 만큼 사전 PROGRAM 수행. 모든 블록 동일 page까지 진행. 해당 의무는 runtime이 아닌 사전 타깃 고정. (의존성 충돌 방지)
  2) Policy READ → DOUT: READ 커밋 시 생성되는 DOUT 의무를 heap 중간에 push 해도 의존성 위반 없음(사전 검증된 READ만 스케줄되기 때문).
  3) AddressManager 기반 사전 스크리닝: heap에 존재하는 미래 타깃 전체를 고려해 policy 단계 후보 중 의존성 불만족을 제거. MUL_ 연산은 plane set 수행 가능성까지 미리 평가. Exclusion은 최후 보루.
  4) Obligation 미이행 시 `hard_slot=True` 로 강제 이행. Latch 보존 덕에 대부분 충족 기대.
  5) `pgmable_ratio`/`readable_ratio` 목표 유지: propose 우선순위에서 obligation 다음 레벨로 고려.
  6) Backoff 삭제: 위 사전 스크리닝과 우선순위 개선으로 대체.
  7) Phase hook 분포화: 훅 자체를 distribution + CFG 커스터마이즈(훅 개수/형태)로 확장.
- **수정 포인트**: `AddressManager.plan_multipane`(사전 필터 보강), `PolicyEngine.propose`(우선순위/백오프 제거), `CFG`(bootstrap/목표치/훅 분포).

---

### 롤아웃/검증
- CFG 마이그레이션 가드: 실행 전 분포 합/키 체계 등 사전검사에서 실패 시 중단.
- 단위테스트:
  - phase_conditional 합/키 검증: 각 키 합=1.0, 음수 가중치 금지, `OP.PHASE.POS` 키 사용 시 예외 발생, `OP.PHASE`/`DEFAULT`만 허용
  - 샘플링 재현성: `_seed_rng_from_cfg` 이후 `random`/`numpy.random` 모두 동일 시드 적용되어 선택 결과가 재현됨
  - selection override 유지: `get_phase_selection_override`는 `OP.PHASE.POS` 키를 정상 해석(phase_conditional와 무관)
  - admission 단순화: `get_admission_delta`가 op별/기본 Δ만 사용하고 phase별 Δ가 무시됨을 확인
  - plan_multipane 규칙: READ는 교집합 페이지, PROGRAM은 다음 페이지/또는 0, ERASE는 비소거 우선 선택 반환 확인 및 `scope` 정확성
  - precheck 규칙: PROGRAM은 `future+1`만 허용, READ는 `(block,page)` 커밋 필요, plane-consistency 위반 시 거부
  - reserve/commit 상태 불변식: `-1 ≤ committed ≤ future < pages_per_block`, PROGRAM/ERASE 후 상태 전이 검증
  - Latch 규칙: READ→DOUT 사이 해당 (die,plane)에 대해 READ/PROGRAM/ERASE 거부, DOUT/SR 허용
  - Exclusion 규칙: DOUT이 GLOBAL ANY 배제창을 생성하며, 분산된 DOUT들 사이에 겹침이 없으면 상호 허용됨
  - DOUT 분산: 멀티플레인 READ 완료 시 `on_commit`이 plane 오름차순으로 DOUT 의무를 생성하고 `plane_stagger_us` 만큼 deadline이 증가함
  - Bootstrap 체인(계획 반영 시): ERASE→PROGRAM→READ→DOUT 순으로 사전 생성된 의무들이 순차 스케줄되고 precheck/bus/latch/exclusion/admission 규칙을 모두 통과
  - Bootstrap DOUT 중복 방지: `op.meta.source=="bootstrap"` 또는 플래그로 on_commit 시 중복 DOUT 생성이 스킵됨
- 메트릭: 의무 in-time 달성률, propose 수락률, 기아(장시간 미스케줄) 지표.



---


## 9) Bootstrap 구현 방안 (Scheduler가 ERASE→PROGRAM→READ→DOUT 전체를 실제 Operation으로 등록)

- **목표**: 초기 상태를 실제 오퍼레이션 체인(ERASE→PROGRAM→READ→DOUT)으로 구성하여, 모든 사전 체크 규칙(precheck/bus/latch/exclusion/admission)을 동일하게 적용하고 의존성을 엄격히 보장.

- **CFG 스키마(제안)**:
  - `bootstrap.enabled: bool` (기본 false)
  - `bootstrap.pgm_ratio: float` (0.0~1.0; 예: 0.10) → 각 블록에서 프로그램·읽을 페이지 수 `k = floor(pages_per_block * ratio)`
  - `bootstrap.fanout: int` (기본 1; 초기엔 단일 plane)
  - `bootstrap.deadline_window_us: float` (예: 50.0; 체인 시작 오프셋)
  - `bootstrap.stage_gap_us: float` (예: 0.2; ERASE→PROGRAM→READ→DOUT 사이 간격)
  - `bootstrap.stagger_us: float` (예: 0.5; plane/블록/페이지별 데드라인 계단 간격)
  - `bootstrap.hard_slot: bool` (기본 true; 의무 우선 소비 보장)
  - `bootstrap.safety.max_ratio: float` (상한; 예: 0.5)

- **생성 타이밍**: `main()`에서 RNG/CFG 적용 이후, `AddressManager` 준비 직후 `populate_bootstrap_obligations(cfg, addr, obl)` 실행

- **타깃 계획 알고리즘(단일 die/plane 우선)**:
  1) `k = clamp(floor(pages_per_block * pgm_ratio), 0, pages_per_block-1)`
  2) 각 plane의 stripe 블록을 순회하며, 각 블록에 대해 `p in [0..k-1]` 페이지를 부트스트랩 대상으로 선택
  3) 각 (block, page p)에 대해 체인 생성:
     - ERASE 의무(조건부): 현재 커밋/미래 상태가 -1이 아니면 추가. `require=ERASE`, `targets=[Address(d, plane, block, 0)]`
     - PROGRAM 의무: `require=PROGRAM`, `targets=[Address(d, plane, block, p)]`
     - READ 의무: `require=READ`, `targets=[Address(d, plane, block, p)]`
     - DOUT 의무: `require=DOUT`, `targets=[Address(d, plane, block, p)]`
     - 모든 의무의 `hard_slot = bootstrap.hard_slot`

- **데드라인/순서 보장(전부 사전 계획 후 heap에 push)**:
  - 기준시간 `t0 = addr.available_at(d, plane) + bootstrap.deadline_window_us + idx*stagger_us`
  - ERASE.deadline = `t0`
  - PROGRAM.deadline = `t0 + stage_gap_us`
  - READ.deadline = `t0 + 2*stage_gap_us`
  - DOUT.deadline = `t0 + 3*stage_gap_us`
  - 모든 의무를 생성한 뒤 일괄 heap push (힙의 상대 순서는 deadline이 결정)

- **사전 체크 만족 전략(체인 설계 상 보장)**:
  - ERASE: precheck에 페이지 제약 없음. 수행 시 해당 블록 future/committed=-1로 초기화되어 이후 PROGRAM 준비 완료.
  - PROGRAM: precheck는 "다음 페이지" 요구 → ERASE 이후 page 0부터 순차가 되므로 페이지 p는 p-1까지 커밋/미래가 선행되어야 함. 이를 위해 같은 블록의 페이지 p에 대해 p>0이면 동일 블록의 p-1 PROGRAM/READ/DOUT 체인이 먼저 deadline(작은 시간)을 갖도록 스태거 인덱스를 증가시키지 않고 페이지 순서만으로 우선화하거나, 페이지별 `t0`에 추가 오프셋 `p*stage_gap_us`를 더해 시간상 선행을 강제.
  - READ: precheck는 커밋 데이터 요구 → 해당 page의 PROGRAM이 완료되어야 함. 위 시간 설계로 충족.
  - DOUT: READ 완료 후 의무 충족. READ보다 늦은 deadline으로 배치.
  - Latch: READ→DOUT 사이 latch 유지로 순서 일관성 보장. (DOUT 전역 배제창은 스케줄 시점에 등록됨)

- **중복 DOUT 방지**:
  - READ 완료 시 기본 로직이 DOUT 의무를 생성한다. 부트스트랩에서 미리 DOUT 의무를 생성했으므로 중복 생성을 피하기 위해, 부트스트랩 의무에는 `meta.source = "bootstrap"` 플래그를 부여하고, `ObligationManager.on_commit`에서 동일 타깃/윈도에 대해 중복 생성을 스킵하는 가드를 추가(필수 항목).

- **멀티플레인 구현**:
  - `plane_set = list(range(planes))`를 고정으로 사용해 전 연산을 멀티플레인으로 수행한다.
  - TARGETS 구성: 각 plane에 대해 해당 stripe의 선택 블록(초기엔 `write_head`)을 사용, 페이지 p는 모든 plane에서 동일하게 설정.
  - PROGRAM은 `scope=DIE_WIDE`, READ는 `scope=PLANE_SET`로 스케줄되며, DOUT은 `scope=NONE`이지만 `targets` 전체를 포함해 래치 해제를 보장한다.
  - 주의: DOUT은 글로벌 배제창(ANY)을 생성하므로, `stage_gap_us`와 `stagger_us`를 통해 과도한 전역 정지를 피하도록 시간 배치 권장.

- **검증 항목**:
  - 모든 체인(ERASE→PROGRAM→READ→DOUT)이 게이트를 통과해 순서대로 스케줄되는지(거부 사유 로그 최소화)
  - on_commit에서 부트스트랩 DOUT과의 중복 생성이 억제되는지
  - 초기 propose 수락률 상승, pgmable/readable 버킷 정상화


---

## 항목 간 충돌/정합성 검토 (PRD 기준)

- phase_conditional 키 축약(OP.PHASE.POS 제거) vs 기존 CFG 키(PRD 참고)
  - 영향: `READ.CORE_BUSY.START` 등 POS 포함 키는 사전검사에서 실패함.
  - 해소: CFG 키를 `OP.PHASE`로 이관하고 합=1.0을 만족시켜야 함.

- admission phase_overrides 제거 vs PRD의 "phase/op별 Δ" 서술
  - 영향: 코드가 op별 Δ/기본 Δ만 사용. phase별 Δ는 무시됨.
  - 해소: PRD를 op별 Δ 중심으로 갱신. CFG에서 phase_overrides 제거.

- 부트스트랩 멀티플레인 체인 vs DOUT 글로벌 배제창
  - 영향: 다수 DOUT 동시 발생 시 전역 정지 빈발.
  - 해소: `stage_gap_us`/`stagger_us`로 DOUT 시점을 분산. 필요 시 DOUT DATA_OUT 축소.

- 멀티플레인 PROGRAM(DIE_WIDE) 배제 vs 연속 체인 진행
  - 영향: PROGRAM CORE_BUSY 동안 동일 die의 READ/PROGRAM/ERASE 차단 → 파이프라이닝 저하.
  - 해소: p(페이지) 단위 순차 체인으로 스태거. fanout=planes는 유지하되 시간 계단을 크게.

- 부트스트랩 DOUT 사전 생성 vs on_commit DOUT 자동 생성
  - 영향: 동일 타깃/윈도 중복 DOUT 의무 생성 가능.
  - 해소: 부트스트랩 의무 `meta.source=bootstrap` 마킹 + on_commit 중복 스킵 가드(필수).

- RNG 혼용(random vs numpy)
  - 영향: 시드가 달라 재현성 저하.
  - 해소: 단일 RNG로 통일하거나 양쪽 시드를 동일하게 설정하고 경로 문서화.
