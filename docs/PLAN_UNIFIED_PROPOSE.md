## Unified Propose 설계: 단일 경로 + 시퀀스 샘플링

### 배경/목표
- 현재는 의무(Obligation) 단계와 정책(phase-conditional) 단계가 분리되어 있어 경합/우선순위 정합성 관리가 복잡함.
- 궁극적으로 propose 경로를 단일화하고, 단일 오퍼레이션과 연속 오퍼레이션(시퀀스) 모두 `phase_conditional`에 정의된 확률로 샘플링하여 제안한다.
- 런타임(on_commit) 의무 생성은 제거하고, 필요한 후속 작업은 시퀀스로 사전 생성/관리하여 heap 소비 순서를 안정화한다.

### 핵심 아이디어
- 단일 경로: `PolicyEngine.propose`는 한 단계만 실행한다. 후보 집합에는
  - 정적 후보: `phase_conditional`에 정의된 단일/멀티플레인 오퍼레이션(SIN_/MUL_ 등)
  - 동적 후보: 현재 시퀀스 큐(기존 Obligation 대체)의 "헤드 아이템"(예: DOUT 헤드)들
  을 함께 포함한다.
- 샘플링: 정적/동적 후보를 하나의 분포로 합쳐 사전 스크리닝(배제/버스/래치/스코프)을 통과한 항목만 대상으로 룰렛 샘플링.
- 시퀀스: 샘플 결과가 시퀀스 생성형일 경우, 그 자리에서 후속 오퍼레이션들을 시퀀스로 미리 생성하여 큐(힙)에 등록하고 헤드만 즉시 스케줄. 이후 헤드만 소비되도록 pop 가드(head-only)를 둔다.
- 런타임 의무 제거: READ 완료 시 on_commit에서 DOUT 생성하지 않음(bootstrap 예외는 유지 가능). 모든 후속은 시퀀스 생성 시간에 확정.

### 후보 집합 구성(단일 경로)
1) 레퍼런스 시각/라벨 계산
   - `used_label = state_timeline.state_at(...)`가 있으면 그것을, 없으면 현재 `hook.label` 사용.
2) 정적 분포 취득
   - `dist = get_phase_dist(cfg, used_label)` → `{"SIN_READ": w, "MUL_READ": w, ...}`
   - 필요 시 시퀀스용 별도 항목(예: `"SEQ.READ_TO_DOUT"`)도 동일 분포에 정의 가능.
3) 동적 분포 병합
   - 해당 die/plane, horizon 내에서 유효한 "시퀀스 헤드"들을 스캔해 동적 후보로 편입.
   - 가중치: `phase_conditional`의 기본 w에 `priority_boost(deadline-now)`를 곱하여 near-deadline 항목의 선택 가능성을 크게 한다. (예: `boost = 1 + k * exp(-Δt/τ)` 혹은 기존 `priority_boost` 파라미터 사용)
4) 사전 스크리닝
   - 각 후보에 대해 예상 구간 `(t0=earliest_start, t1=t0+nominal_dur)`로 아래 게이트 적용:
     - exclusion(`_excl_blocks_candidate`), bus(`state_timeline.overlaps` + `addr.bus_precheck`), 스코프 혼잡, latch(`latch.allowed`), admission
   - 통과한 항목만 최종 후보.
5) 샘플링/제안
   - 룰렛 피킹 후, 단일 오퍼레이션이면 즉시 스케줄. 시퀀스 생성형이면 시퀀스를 생성하고 헤드를 스케줄.

### 시퀀스 생성/소비 흐름
- 생성 시점: 단일 경로 propose에서 시퀀스형 항목이 샘플되었을 때, 또는 특정 단일 op(예: SIN_READ)가 샘플되었지만 정책상 시퀀스 전개가 필요할 때.
- 생성 내용: 예) `READ → (DOUT [+ SR])`
  - 타깃: READ 타깃을 기준으로 후속 오퍼레이션 당 plane/page 유지.
  - 기한(deadline): READ의 예상 `end_us` + 간극(`gap/stagger`) + nominal duration 기반.
  - 메타: `sequence_id`, `sequence_pos`, `sequence_len` 태깅.
- 소비: 큐(pop)는 동일 `sequence_id`의 헤드(`sequence_pos == head_index`)만 선택 가능. 비-head는 항상 스킵/보관 후 재삽입.
- 훅 주입: 각 후속(예: DOUT) deadline 직전에 `PHASE_HOOK`를 plane별로 주입해 제안 기회를 보장.

### 시간축/버스/래치 가드
- 제안 시(사전)와 스케줄 직전(최종) 두 단계에서 동일 가드를 적용하여 정합성 유지.
- 시퀀스 생성 시 "미래 유효성" 검사: 예상 시각으로 동일 가드를 적용해, 불가한 후속은 생성하지 않거나(권고) 전체 시퀀스 생성을 취소.
- READ→DOUT 사이 `LatchManager`로 래치 보존을 유지하여 순서 안정성 확보.

### 구성/스키마 변경안
- 전환 토글
```yaml
policy:
  sequence_mode: true            # 단일 경로 + 시퀀스 샘플링 활성화
  enable_phase_conditional: true # 유지
  hookscreen:
    horizon_us: 0.30
    global_obl_iters: 1
  # (선택) 동적 헤드 가중치 부스트 파라미터
  seq_head_boost:
    k: 4.0        # 부스트 강도
    tau_us: 2.5   # 마감 임박 민감도
```

- phase_conditional: 시퀀스 항목 병행 정의(예시)
```yaml
phase_conditional:
  "READ.END":
    SIN_READ: 0.20
    MUL_READ: 0.10
    SEQ.READ_TO_DOUT: 0.30  # 시퀀스 생성형 항목
    SR: 0.05
  DEFAULT:
    SIN_PROGRAM: 0.10
    MUL_PROGRAM: 0.05
    SIN_ERASE: 0.10
    MUL_ERASE: 0.05
    SEQ.READ_TO_DOUT: 0.10
```

- 시퀀스 템플릿(새 섹션)
```yaml
sequence_templates:
  READ_TO_DOUT:
    head: SIN_READ
    followups:
      - kind: DOUT
        gap_us: 0.2           # READ.end 이후 간극
        stagger_by_plane: true
        priority_boost:
          hard_slot: true
          start_us_before_deadline: 2.5
          plane_stagger_us: 0.2
```

- 런타임 생성 차단
```yaml
obligations:
  # 기존 항목 유지 가능하나, sequence_mode=true일 때 on_commit 생성은 무시
admission:
  obligation_bypass: true   # 유지(시퀀스 헤드도 near-future 게이트 우회 가능 옵션)
```

### 구현 포인트(코드 변경 개요)
- `Obligation` → 시퀀스 메타 추가: `sequence_id/pos/len` (또는 별도 `SequenceItem` 도메인 타입)
- `ObligationManager` → `SequenceQueueManager`로 활용(명칭 유지 가능)
  - `create_sequence_from(op|plan)` 구현: 템플릿/타깃/nominal 기반으로 후속 전개 및 heap 등록
  - `pop_urgent` → head-only 선택 가드 추가, 비-head는 항상 재삽입
  - `on_commit` 런타임 생성 비활성(bootstrap 예외)
- `PolicyEngine.propose`
  - 단일 경로로 통합: dist + 동적 헤드 병합 → 스크리닝 → 샘플 → 실행
  - 샘플 결과가 시퀀스형이면 `create_sequence_from(...)` 호출 후 헤드 스케줄
- `Scheduler._schedule_operation`
  - 기존 fail-safe 가드 유지
  - 헤드/후속 deadline 직전 `PHASE_HOOK` 주입

### 리스크/완화
- 마감 위기 항목 기아: 동적 헤드에 부스트를 주고, hard_slot/horizon 무시 옵션으로 보장.
- 힙 폭증: 시퀀스 길이/동시 시퀀스 수에 상한. 생성 시 정적 타당성 검사를 보수적으로 적용.
- 미래 시각 오차: 예상 시각을 `earliest_start_for_scope` 기반 보수화, 거절 시 시퀀스 전체 취소/재시도.
- 회귀: `sequence_mode` 토글로 안전 롤백. bootstrap 경로는 기존 로직 유지.

### 점진 이행(권고)
1) 데이터 모델/큐 가드(head-only) 도입 → 2) on_commit 생성 토글화 → 3) 동적 헤드 병합 샘플링 → 4) 시퀀스 템플릿 기반 생성 → 5) 훅 보강/튜닝.

### 지표/검증
- `reject_log.csv`: `sequence_guard`, `deadline`, `bus`, `excl` 비중 감소 확인.
- `nand_timeline.csv`: READ→DOUT 순서 및 간극 준수, 충돌 로그 감소.
- `propose accept ratio` 상승, `fulfilled_in_time` 개선.

### 참고(현 구조와의 정합)
- Bootstrap은 이미 연쇄 의무 사전 생성 구조로, 본 시퀀스 설계와 철학이 일치.
- 기존 stage 구분(의무/phase_conditional)은 통계/로깅 목적만 유지하거나 단일 스테이지로 축소 가능.

