### PLAN_EASING_HOOKSCREEN

#### 배경/문제
- 현재 phase hook은 본래 “오퍼레이션 흐름을 끊지 않고, obligation/policy 제안을 트리거”하는 용도이나,
  구현상 훅의 `die/plane`가 강한 스크리닝으로 작용하여 제안 거절(`none_available`)과 기회손실을 유발함.
- 주요 스크리닝 지점:
  - pop_urgent: `same_die && same_plane` 필수 → 훅 plane과 의무 타깃 plane가 다르면 소비 보류
  - policy: `plan_multipane(..., start_plane=hook_plane)` 편향 → 후보 축소
  - earliest_start: 훅 plane 기반으로 산출된 값 사용 → 타 plane 가능성 차단
  - plane 단위 훅 폭주로 인한 전역 상태 반영 지연

---

## 목표
- 훅을 “전역 트리거”로 재해석하여, 훅 plane 불일치로 인한 불필요한 거절을 줄이고
  obligation 우선 처리와 안정적 스케줄링을 보장.

---

## 개선 항목(단계별)

1) pop_urgent 완화(plane 스크리닝 완화)
- 변경: `same_plane`를 기본 False 허용으로 낮추고, `same_die && in_horizon && feasible`이면 선택 대상에 포함.
- Plane 바인딩: 선택 후 실제 스케줄 직전(또는 build 시점)에 해당 `ob.targets`의 plane_set 기준으로 `earliest_start_for_scope` 재계산.
- 이점: 훅 plane 불일치로 인한 `none_available` 감소, 의무 소비 가속.
- 리스크: (현 구조 기준) 단일 이벤트 루프로 heap pop이 순차 처리되어 동일 의무의 중복 소비 위험 없음. 다만 향후 병렬/동시성 도입 시 위험 재발 가능.
- 완화책: 현 구조에서는 불필요. 향후 병렬화 시 힙 접근 락/원자적 pop-assign 유지로 방지.

2) policy의 start_plane 의존 완화
- 변경: `plan_multipane(kind, die, hook_plane, ...)` 호출 실패 시, `start_plane`을 전 plane로 라운드로빈 탐색하여 대안 시도(시도 횟수 제한).
- 이점: 훅 plane 편향 완화로 `plan_none` 감소.
- 리스크: 연산량 증가.
- 완화책: `planner_max_tries` 상한 적용, 1회 대안 탐색만 허용.

3) earliest_start 재산정
- 변경: obligation/policy에서 후보가 정해진 뒤, admission/feasible 판단 전에 `AddressManager.candidate_start_for_scope(now, die, scope, plane_set)`로 재산정하여 훅 plane 기반 편향 제거.
- 이점: 훅 plane 바쁨으로 인한 불필요한 불가 판정 감소.
- 리스크: 미세한 시각 차이로 스케줄 순서 변화.
- 완화책: quantize 동일 사용, fail-safe 사전/직전 점검 유지.

4) 전역 트리거 보강(GLOBAL.NUDGE)
- 변경: 주기적 `GLOBAL.NUDGE` 훅(다이/플레인 무관)을 추가하여, 의무/정책 제안의 전역 기회를 주기적으로 부여.
- 이점: plane 훅 간격이 커도 전역 상태를 주기적으로 소거.
- 리스크: 이벤트 수 증가.
- 완화책: `queue_refill_period_us`와 별도 주기를 작게 유지하지 않고, 적정 주기(예: 2~3x REFILL)로 제한.

5) 부트스트랩 기간 policy skip 유지 + 의무 드레이닝 가속
- 변경: 부트스트랩 의무가 존재하면 policy 분기 skip(이미 구현) + pop_urgent 완화로 소비 가속.
- 이점: 초기 단계 정책 개입 최소화, 목표 상태 수립 후 정책 개입.
- 리스크: 의무 폭주 시 장시간 policy 정지.
- 완화책: `obl_lock_period` 최대치 설정, watchdog 로깅.

---

## 리스크와 완화책

- 공정성/기아
  - 리스크: 전역화로 특정 plane에 의무/정책이 몰릴 수 있음.
  - 완화: plane 라운드로빈, selection override 유지, 가중치 기반 우선순위 튜닝.

- 전역 배제/래치 창 충돌
  - 리스크: 전역화로 동시성 높아지며 DOUT ANY 창 겹침 우려.
  - 완화: DOUT plane 스태거 유지/확대, schedule-time fail-safe 유지, 사전 bus/excl 점검 유지.

- 행동 변화로 인한 회귀
  - 리스크: 기존 시퀀스/메트릭 변동.
  - 완화: A/B 플래그(`easing_hookscreen.enabled`)로 토글, 메트릭 비교(accept ratio, none_available 비율, in-time율).

---

## 구현 가이드(요약)
- 플래그 추가: `policy.easing_hookscreen: { enable: true, startplane_scan: 1 }`
- pop_urgent: `same_plane` 완화(옵션), 선택 후 scope 기반 earliest 재산정.
- policy: plan 실패 시 start_plane 라운드로빈 대안 N회.
- propose: admission/feasible 판단 직전 scope 기반 재산정 사용.
- 훅: `GLOBAL.NUDGE` 주기 훅 추가(옵션).

---

## 검증/메트릭
- none_available 감소율, obligation drain 시간, policy accept ratio 변화
- DOUT 배제창 겹침 비율, bus/latch/excl reject 비율 변화


