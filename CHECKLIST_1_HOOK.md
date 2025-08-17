### CHECKLIST_HOOK (Easing Hook-screen Plan)

#### A. 플래그/CFG
- [x] `policy.easing_hookscreen.enable` 추가 (기본 false)
- [x] `policy.easing_hookscreen.startplane_scan` 추가 (기본 1)
- [x] `queue_refill_period_us`와 별개로 `global_nudge_period_us`(옵션) 추가
- [x] `policy.easing_hookscreen.global_obl_iters` 추가(전역 훅 강도)

#### B. Obligation(pop_urgent) 완화
- [x] `same_plane` 완화(옵션화): `same_die && in_horizon && feasible` 충족 시 선택 허용(플래그 연결)
- [x] 선택 후 scope 기반 `earliest_start_for_scope` 재산정 적용
- [x] 단일 이벤트 루프에서 중복 소비 없음 확인(선택 즉시 heap pop/mark_assigned)
- [x] `easing_hookscreen.horizon_us` 옵션 추가 및 적용

#### C. Policy plan 개선
- [x] `plan_multipane(..., start_plane=hook_plane)` 실패 시, 라운드로빈으로 대안 start_plane 최대 N회 시도 (`startplane_scan`)
- [x] 시도 횟수 상한/조기 중단 로직 반영

#### D. earliest_start 재산정
- [x] obligation/policy 모두 후보 확정 후 `AddressManager.candidate_start_for_scope(now, die, scope, plane_set)`로 start_hint 재산정
- [x] 재산정 값으로 `_admission_ok/feasible` 검사 수행
- [x] schedule-time fail-safe 유지(bus/latch/excl)

#### E. GLOBAL.NUDGE 훅(옵션)
- [x] 주기적 전역 훅(`GLOBAL.NUDGE`) 생성 로직 추가(토글 가능)
- [x] 훅 핸들러에서 obligation→policy 순으로 propose 호출(현행 동일)
- [x] 주기 상한 적용(REFILL의 2~3배 권장)

#### F. 부트스트랩 기간 동작
- [x] 부트스트랩 의무 존재 시 policy 분기 skip 유지(현행)
- [x] pop_urgent 완화로 부트스트랩 의무 drain 가속 확인
- [x] watchdog 로깅(부트스트랩 지속/소비율)

#### G. 테스트(단위/통합)
- [ ] none_available 감소 테스트(전/후), obligation drain 시간 단축 검증
- [ ] policy accept ratio 변화(전/후)
- [ ] DOUT GLOBAL ANY 겹침 비율 변화(전/후)
- [ ] bus/latch/excl reject 비율 회귀 테스트
- [x] 부트스트랩 기간 policy skip 보장 테스트
- [x] 병렬화 비적용 환경에서 중복 소비 없음 확인

#### H. 메트릭/관측
- [x] stage별 attempts/accepts/rejects 비율 수집
- [x] `none_available`/`bus`/`precheck`/`latch`/`excl` 상위 사유 Top-N 로깅(파일 저장 포함)
- [x] obligation in-time 달성률/소비시간

#### I. 롤아웃
- [ ] 플래그 기본 off로 머지 → 내부 스위치로 A/B 검증
- [ ] PRD/PLAN 문서 갱신(훅 전역 트리거 해석 및 스크리닝 완화 명시)
- [ ] 체크리스트 항목 완료 후 기본 on 고려(지표 기준 충족 시)


