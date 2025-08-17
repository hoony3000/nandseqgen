### CHECKLIST_1 (실행 계획)

#### A. CFG/사전검사
- [ ] phase_conditional 키 OP.PHASE/DEFAULT로만 정리 (OP.PHASE.POS 제거)
- [ ] phase_conditional 각 분포의 합=1.0, 음수 가중치 없음 검증 로직 통과
- [ ] admission.phase_overrides 제거 및 op_overrides/default만 사용하도록 CFG/코드 일치

#### B. RNG/샘플링 (진행)
- [x] `_seed_rng_from_cfg`에서 `random.seed`와 `numpy.random.seed` 동일 시드 적용
- [ ] `roulette_pick` 가중치 샘플링 경로 일관 동작 확인(필요 시 np.choice로 대체 계획 유지)

#### C. Policy/Selection (진행)
- [x] Backoff 단계 제거(코드/문서 일관)
- [x] `get_phase_dist`가 OP.PHASE/DEFAULT만 조회하도록 적용됨
- [ ] `get_phase_selection_override` 동작 유지(필요 시 OP.PHASE.POS 키만 selection에 한해 허용 여부 재확인)
- [x] `_admission_ok/get_admission_delta`가 op별 Δ/기본 Δ만 사용

#### D. Obligation 동작 (진행)
- [x] `ObligationManager.on_commit`에서 멀티플레인 READ 완료 시 DOUT 의무를 plane 오름차순으로 deadline 분산 생성
- [x] `priority_boost.plane_stagger_us`(없으면 기본 0.2us) 적용 확인
- [x] 부트스트랩 소스의 READ에 대해서는 DOUT 자동 생성 스킵(`meta.source=="bootstrap"` 또는 플래그)
- [ ] `obligation_bypass=True`일 때 의무의 admission 우회 동작 확인

#### E. Latch/Exclusion (진행)
- [x] `LatchManager.allowed`: "ERASE/PROGRAM/READ vs 그 외" 두 갈래로 동작
- [ ] READ→DOUT 구간 래치 보존, DOUT/SR 허용, DIE_WIDE/PLANE_SET 범위 판정 정상
- [ ] DOUT GLOBAL ANY 배제창과 DOUT deadline 분산이 충돌하지 않도록 겹침 없음 확인

#### F. Address/Plan/Precheck
- [ ] `plan_multipane`: READ 교집합 page, PROGRAM 다음 page(또는 0 후보), ERASE 비소거 우선, SR 단일 plane
- [ ] `precheck_planescope`: plane-consistency, PROGRAM `future+1`, READ 커밋 존재, scope별 시간 겹침 금지
- [ ] `reserve_planescope/bus_reserve/register_future/commit` 순서/효과 검증(가용시각/버스/상태 전이)

#### G. Bootstrap 체인(멀티플레인 필수)
- [x] `bootstrap.enabled/pgm_ratio/fanout(=planes)/deadline_window_us/stage_gap_us/stagger_us/hard_slot` CFG 추가(기본 disabled)
- [x] 각 (block, page p)마다 ERASE→PROGRAM→READ→DOUT 의무 생성(멀티플레인 plane_set 고정) 로직 추가
- [x] DEADLINE: `t0 + {0, gap, 2*gap, 3*gap} + idx*stagger`로 체인/plane 간 분산 생성
- [x] 모든 의무 heap 일괄 push; 힙 순서 불변 원칙 준수
- [x] on_commit 중복 DOUT 생성 스킵 가드 연동(op.meta 소스/플래그)
- [x] main()에서 bootstrap 의무 주입 호출 적용

#### H. 테스트/메트릭
- [ ] phase_conditional 합/키/음수/예외 테스트
- [ ] RNG 재현성 테스트(random/numpy 동시 시드)
- [ ] plan_multipane/ precheck/ reserve-commit 불변식 테스트
- [ ] Latch/Exclusion 규칙 테스트(READ→DOUT, DOUT GLOBAL 창 겹침 없음)
- [ ] 멀티플레인 READ 후 DOUT 분산 생성 및 deadline 스태거 검증
- [ ] Bootstrap 체인 순차 스케줄 및 규칙 통과/중복 DOUT 방지 테스트
- [ ] 수락률/의무 in-time/기아 지표 수집 및 회귀 기준 설정

#### I. 롤아웃 순서
- [ ] CFG/문서 갱신(PRD/PLAN/README)
- [ ] 코드 수정(A~G 순으로 소단위 커밋)
- [ ] 단위테스트 추가/통과 확인
- [ ] 시뮬레이션 실행 및 메트릭 확인

