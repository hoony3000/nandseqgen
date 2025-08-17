### PLAN: Bootstrap 파라미터 정리와 Exit 전략(슬림 버전)

- 대상 코드: `nandsim_demo.py` 전반(특히 `populate_bootstrap_obligations`, `ObligationManager`, `PolicyEngine`, `Scheduler`)
- 목표: (1) Bootstrap 파라미터를 코드 기준으로 정확히 나열, (2) 핵심 파라미터만 남기는 슬림 스키마 제안, (3) Exit 시퀀스 명료화

---

### 1) 현행 Bootstrap 파라미터 일람(코드 기준)

- 사용 소스: `populate_bootstrap_obligations()` 내부 `bs.get(...)` 및 관련 가드/워치독

- **핵심 파라미터**
  - `bootstrap.enabled: bool` 기본 False — 부트스트랩 on/off
  - `bootstrap.pgm_ratio: float` 기본 0.0 — 각 블록당 준비할 페이지 비율(0.0~1.0)
  - `bootstrap.max_ratio: float` 기본 0.5 — `pgm_ratio` 상한
  - `bootstrap.deadline_window_us: float` 기본 50.0 — 체인 시작 오프셋
  - `bootstrap.stage_gap_us: float` 기본 0.2 — ERASE→PROGRAM→READ 간 간격
  - `bootstrap.stagger_us: float` 기본 0.5 — plane/페이지 간 데드라인 계단 폭
  - `bootstrap.hard_slot: bool` 기본 True — ERASE/PROGRAM 의무를 하드 슬롯로 생성

- **선택/미세조정 파라미터**(있으면 사용, 없으면 기본)
  - `bootstrap.dout_stagger_n: float` 기본 0.0 — DOUT 스태거를 `N × DOUT_nominal`로 증폭(0이면 미사용)

- **비핵심/파생**
  - `bootstrap.fanout: "planes"` — 제거됨(모든 plane 사용으로 고정)
  - 토폴로지 파라미터(`topology.dies/planes/blocks/pages_per_block`) — 대상 공간 및 페이지 수 산정에 사용(부트스트랩 고유 파라미터 아님)
  - 워치독: `PolicyEngine.propose()`는 `obl.has_pending(source="bootstrap")` 동안 정책 제안 스킵(부트스트랩 보호 가드)

---

### 2) 슬림 스키마 제안(핵심만 유지)

- **유지(핵심)**
  - `enabled: bool`
  - `pgm_ratio: float` + `max_ratio: float`
  - `deadline_window_us: float`
  - `stage_gap_us: float`
  - `stagger_us: float`
  - `hard_slot: bool`(전 의무 공통 적용)

- **선택(운영에 유용, 복잡도 낮음)**
  - `dout_stagger_n: float`(0이면 비활성)

- **폐기/내장 상수화(코드에 기본값 내장 권장)**
  - `hard_slot_read`, `hard_slot_dout` → 단일 `hard_slot`로 일원화(코드 반영 완료)
  - `program_after_erase_eps_us`, `read_after_program_eps_us`, `order_eps_us` → 최소 여유값을 코드 상수(2.0, 2.0, 0.2)로 통일(코드 반영 완료)
  - `fanout` → 제거(모든 plane 사용으로 고정)

- **마이그레이션 가이드(구→신)**
  - `{hard_slot_read, hard_slot_dout} ⇒ hard_slot`
  - `*_eps_us` 계열 제거 ⇒ 코드 기본값 사용
  - `fanout` 제거 ⇒ 모든 plane 대상(현행 동작 유지)

---

### 3) 동작 개요(슬림 스키마 하)

- 대상: 단일 die의 모든 블록에 대해 `k = floor(pages_per_block × clamp(pgm_ratio, 0..max_ratio))` 페이지까지
- 생성 순서: 페이지 p마다 `ERASE(die-wide) → PROGRAM(die-wide) → READ(multi-plane) → DOUT(plane별)` 의무를 데드라인과 함께 힙에 일괄 주입
- 데드라인: `t0 = available_at + deadline_window_us + p*(3*stage_gap_us + stagger_us)`를 기준으로 순차 배치
  - PROGRAM, READ는 명시 여유값(코드 상수화) 반영해 선후 관계 보존
  - DOUT은 `max(stagger_us, dout_stagger_n × DOUT_nominal)`로 plane 간 분산
- 가드: 부트스트랩 의무가 존재하는 동안 정책 제안 스킵(기존 가드 유지)

---

### 3.5) 이번 커밋에 반영된 변경 사항(요약)

- Constraints: DOUT 전역 배제창 제거(버스/래치 검사만 사용)
- Bootstrap 스키마 슬림화: `fanout` 제거, `dout_stagger_n` 유지
- 단일 `hard_slot` 적용: READ/DOUT 별도 하드 슬롯 제거
- 여유값 상수화: `program_after_erase_eps_us=2.0`, `read_after_program_eps_us=2.0`, `order_eps_us=0.2`
- 러닝타임 자동 연장: Bootstrap 활성 시 `run_until_us = max(run_until_us, last_deadline + 7000us)`
- 운영값(예시) 적용: `queue_refill_period_us=50.0`, `easing_hookscreen.enable=True`, `startplane_scan=2`, `horizon_us=0.30`, `global_obl_iters=2` (전역 트리거는 QUEUE_REFILL로 일원화)
- 로깅 축소: `ObligationManager.debug=False` 기본화

---

### 4) Exit 시퀀스(명료화)

- **종료 조건**
  - `obl.has_pending(source="bootstrap") == False` AND `assigned == 0`
  - `created == fulfilled` 확인 후 종료 시각 기록

- **전환 액션**
  - `policy.enable_phase_conditional = True` 복원(필요 시)
  - `policy.easing_hookscreen.enable`은 운영 여건에 맞춰 유지/완화(`startplane_scan`, `horizon_us` 정상값)
  - DOUT 전역 배제창(Constraints)의 `GLOBAL/ANY` 항목은 제거 유지(버스 충돌 검사로 충분)

- **러닝타임 설정**
  - 총 런타임 분리 계산: `run_until_tot = run_until_bootstrap + run_until_base`
  - 제안 산식(코드 반영):
    - `run_until_base = policy.run_until_us`
    - `run_until_bootstrap = quantize(last_deadline_boot + num_bootstrap_obligations * policy.run_until_bootstrap_margin_per_ob_us)`
    - 기본 `policy.run_until_bootstrap_margin_per_ob_us = 3.0`
  - 결과 적용: `sch.run_until(run_until_tot)`

- **관측 지표**
  - 필수: `created==fulfilled==K`, `expired==0`, `dropped==0`
  - 보조: `soft_defer/*` 사유별 추이(특히 `soft_defer/bus` 감소 여부)

---

### 5) 작업 항목(체크리스트)

- [x] CFG 스키마 슬림화 반영: 코드 업데이트(`bootstrap.*`) 및 본 문서 반영
- [x] `dout_stagger_n`만 선택 유지, 나머지 `*_eps_us`는 코드 상수화
- [x] `hard_slot_read/dout` 제거 → 단일 `hard_slot` 적용 확인
- [x] Constraints에서 DOUT 전역 배제창 제거(또는 플래그화) — 버스 충돌 검사로 대체
- [x] `run_until_us`를 `last_deadline + margin`으로 자동 설정(코드 반영)
- [x] 로그 축소 기본값(`ObligationManager.debug=False`) — CSV 중심 유지

---

### 6) 수용 기준

- Bootstrap 종료 시 `created==fulfilled`, `expired==0`, `dropped==0`
- DOUT 전역 배제창 제거 후에도 폭주/교착 없음, `soft_defer/bus` 유의미 감소
- Exit 이후 1 윈도우 동안 수용률/지연 지표가 악화되지 않을 것

---

부가 메모
- `fanout`은 현재 구현에서 모든 plane 사용으로 사실상 고정. 필요 시 후속 스펙에서 복원 가능
- 최소 여유(`*_eps_us`)는 코드 상수로 두고, 특이 워크로드 필요 시에만 CFG로 재노출 검토

