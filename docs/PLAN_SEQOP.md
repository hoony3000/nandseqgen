# PLAN: 연속 Operation 의무(Obligation) 생성·스케줄 설계

## Problem 1-Pager
- 배경: 현재 스케줄러는 단일 의무(DOUT 등)와 phase_conditional 제안 기반으로 동작한다. 호스트가 요청하는 연속 동작(예: sequential read, cache read)을 한 번의 의도(세트)로 표현하고, 순서 보장 하에 스케줄에 반영할 수 있어야 한다.
- 문제: 
  - 연속 동작을 어떻게 의무로 모델링하고, 언제/어디서 생성할지(런타임) 정의가 필요.
  - phase_conditional 단계에서 단일 op 제안 vs 연속 세트 제안을 랜덤하게 선택할 수 있어야 함.
  - 연속 의무가 진행되는 동안, 중간에 다른 제안이 끼어들어 순서를 깨지 않도록 검증/차단이 필요.
- 목표:
  - 연속 의무 세트의 생성·등록·소비(스케줄) 로직 설계 및 삽입 위치 정의.
  - `CFG["op_specs"]` 기반으로 순서를 구성하고 target 규칙(예: page +1)을 강제.
  - phase_conditional와의 상호작용: 무작위로 단일/연속 제안을 선택, 순서 보장 검증.
- 비목표:
  - 대규모 리팩터링(옵션으로 제시), 외부 저장소/네트워크 연동, 성능 최적화의 조기 튜닝.
- 제약:
  - 기존 코드 변경 최소화가 1순위. 스케줄/게이트(버스/Exclusion/Latch/Admission) 정합성 유지.
  - 파일/함수 복잡도 상한 준수(함수 ≤ 50 LOC 권장, 필요 시 분리).

---

## 핵심 개념
- 연속 의무(Sequence of Obligations): 호스트 요청을 하나의 시퀀스로 모델링. 각 단계는 `CFG["op_specs"]`에 등록된 base op(READ/PROGRAM/ERASE/SR 등)로 구성된다.
- 순서 보장: 각 시퀀스의 head 의무만 스케줄 후보가 된다. head가 완료되면 다음(head index+1)이 활성화된다.
- 런타임 생성: phase_conditional 단계에서 확률적으로 “연속 세트 생성”을 선택하면, 즉시 의무 힙에 일괄 등록한다(스케줄은 기존 의무 우선 규칙을 따름).
- 시퀀스 가드(sequence guard): 연속 세트의 head가 임박했거나 같은 die/plane 세트에 영향 줄 후보 op가 제안될 경우, 순서 위배 가능성을 검사·차단한다.

## 데이터 모델 확장
- `Obligation` 메타필드(추가):
  - `seq_id: Optional[int]` / `seq_idx: Optional[int]` / `seq_len: Optional[int]`
  - `seq_policy: Optional[str]` (예: "strict_order")
  - `source: Optional[str]` (예: `"host_seq"`, `"policy.seq"`) — 기존 필드 재사용
  - `group_key: Optional[Tuple[int, Tuple[int,...]]` (예: `(die, plane_set)`)
- `ObligationManager` 내부 구조(최소 변경안):
  - `seq_heads: Dict[int, int]` (시퀀스별 head 인덱스)
  - `seq_items: Dict[int, List[int]]` (시퀀스별 의무 ID 목록)
  - `id_to_seq: Dict[int, Tuple[int, int]]` (ob_id → (seq_id, seq_idx))

## CFG 확장(포맷 B 채택)
선택: CFG["sequences"]는 포맷 B(Generated)를 표준으로 채택한다.

- 대안 비교 요약: 
  - Explicit(A): 직관적/가시성 좋음 | 긴 시퀀스 장황 | 미니 DSL 보안·해석 비용.
  - Generated(B): 선언 간결/재사용 용이 | 런타임 등록 필요 | 변경 파급 리스크.
- 결정: 복잡한 캐시 리드/지그재그 패턴을 간결히 표현하고 재사용·단위테스트를 용이하게 하기 위해 B를 채택.

### 스키마(Format B)
```
CFG["sequences"] = [
  {
    "name": "<SEQ_NAME>",
    "kind": "generated",
    "generator": "<registered_generator_name>",
    "params": { ... },               # 제너레이터별 파라미터
    "window_us": <float>,            # head 마감 기준
    "step_gap_us": <float>           # 각 단계 마감/가드 간격
  },
  ...
]

CFG["sequence"] = {"enable": true, "guard_window_us": 0.5, "p_create": 0.25}
```
- `fanout`/`interleave`/`targets_rule` 등 주소·병렬도 관련 값은 `params`에 포함하거나(권장) 제너레이터가 내부 정책으로 결정한다.
- phase_conditional 분포에 직접 키를 추가하는 대신, 간결성을 위해 `sequence.p_create`로 전역 생성확률을 제어한다.

### 예시(Format B)
```
CFG["sequences"].append({
  "name": "SEQ_READ_PAGES",
  "kind": "generated",
  "generator": "read_pages_linear",
  "params": {
    "base_page": "$input.page",    # 시작 페이지
    "length": 4,                    # step 수
    "fanout": 1,                    # 멀티플레인 허용 시 >1
    "interleave": true              # 인터리브 허용
  },
  "window_us": 8.0,
  "step_gap_us": 2.0
})

CFG["sequences"].append({
  "name": "SEQ_CACHE_READ",
  "kind": "generated",
  "generator": "cache_read_zigzag",
  "params": {
    "base_page": "$input.page",    # N
    "cycles": 8,                    # m
    "ops": {
      "head": "SIN_READ",
      "body_pair": ["SIN_CACHE_READ", "DOUT"],
      "flush": ["CACHE_READ_END", "DOUT"]
    },
    "fanout": 1,
    "interleave": true
  },
  "window_us": 6.0,
  "step_gap_us": 1.0
})
```

### 제너레이터 계약(초안)
- `read_pages_linear(base_page, length, fanout=1, interleave=true)`
  - 단계: `READ` 반복(`length`회), 페이지: `base_page + i`.
- `same_page_ops(page, ops[])`
  - 각 `op`를 동일 `page`에 매핑.
- `cache_read_zigzag(base_page, cycles, ops, fanout=1, interleave=true)`
  - 생성 순서: `head` 1회 → (`body_pair` 반복) `cycles`회 → `flush`.
  - 페이지 시퀀스: `N, N+1, N, N+2, N+1, …, N+m, N+m-1, N+m, N+m`.

실행/호환성: 포맷 B는 런타임에 “평탄화된(step 리스트) 의무 세트”로 변환되어 기존 `ObligationManager`/head-only pop 흐름을 그대로 따른다.
 - `ObligationManager.create_sequence(spec, ...)`에서 등록 제너레이터를 호출하여 `[{op, target, deadline, ...}, ...]`로 전개한다.

## 타깃 규칙(예시)
- Sequential READ(page+1):
  - step0: `AddressManager.plan_multipane(READ, die, start_plane, fanout, interleave)`로 공통 page p와 plane 세트 확보.
  - step i>0: plane 세트 고정, 각 타깃의 page를 `p+i`로 설정. 불가 시 시퀀스 생성 취소.
- Cache READ: 위와 같되 length/step_gap을 더 짧게.

## 생성·스케줄 흐름
1) PHASE_HOOK 수신 → `PolicyEngine.propose(...)` 진입.
2) 의무 우선(pop_urgent) 시도: 시퀀스 head만 후보가 되도록 `pop_urgent`에서 비-head는 모두 스킵.
3) (의무 실패 시) phase_conditional로 진입:
   - `sequence.enable`이고 `rand < p_create`면 시퀀스 템플릿 하나를 선택해 `ObligationManager.create_sequence(...)` 호출 후 None 반환(다음 훅에서 의무가 소비됨).
   - 단일 op 제안 경로를 선택하면, 수락 직전 `sequence_guard_allow(...)` 검사로 순서 위배 가능성을 차단.

## 구현 포인트(최소 변경 경로)
- `nandsim_demo.py`
  - `Obligation` dataclass 확장(메타 필드들 Optional 추가).
  - 제너레이터 레지스트리 도입(간단 맵):
    - `SequenceGenerators = { str name: Callable(params) -> List[StepSpec] }`
    - 최소 구현: `read_pages_linear`, `same_page_ops`, `cache_read_zigzag` 등록.
  - `ObligationManager`에 다음 메서드 추가/수정:
    - `create_sequence(spec, die, start_plane, now_us)` → `spec.generator`로 레지스트리 lookup 후 평탄화.
    - `sequence_guard_allow(op, start_hint, now_us)`
    - `mark_fulfilled(...)`에서 head 진전 및 시퀀스 종료 정리
    - `expire_due(...)`에서 head 만료 시 후속 아이템 정리
    - `pop_urgent(...)`에서 비-head 스킵
  - `PolicyEngine.propose(...)` phase_conditional 분기:
    - 무작위로 시퀀스 생성 호출(선택 시 바로 반환)
    - 단일 op 수락 직전 `sequence_guard_allow` 검사 및 reject(`reason="sequence_guard"`)

## 순서 보장 메커니즘
- Head-only pop: `pop_urgent`가 `id_to_seq`를 참고하여 head가 아닌 의무는 무조건 keep.
- Guard: 동일 die에서 head가 존재하고 `deadline_head - now <= guard_window_us`이면,
  - (i) 후보 op가 같은 plane_set을 점유하거나
  - (ii) 후보 scope가 `DIE_WIDE`이거나 bus 상충 구간을 선점한다면
  - → 해당 후보를 `sequence_guard` 사유로 거절.

## phase_conditional 상호작용
- 생성 타이밍: phase_conditional 진입 시점(의무 실패 직후)에서 RNG로 단일/연속 선택. 연속 선택 시 즉시 의무 힙에 등록.
- 단일 제안 수락 전 검사: `sequence_guard_allow`로 순서 위배 방지.

## 두 가지 설계 대안
1) 최소 변경안(ObligationManager 확장 + PolicyEngine 가드/생성 호출)
- 장점: 변경 범위 작고 기존 흐름(의무 우선)을 그대로 사용. CFG로 손쉬운 온/오프.
- 단점: ObligationManager가 단일/연속 개념을 함께 관리하여 약간의 복잡도 증가.
- 위험: 
  - head 판별 누락 시 순서 위배 가능 → 비-head 스킵 로직/테스트로 완화.
  - guard 튜닝 필요(과도 차단/기아) → `guard_window_us`로 완화.

2) 구조 변경안(SequenceManager 신설)
- 구조: SequenceManager가 시퀀스 템플릿/생성/헤드관리/가드를 전담, ObligationManager는 순수 의무 힙 유지.
- 장점: 관심사 분리, 테스트 용이, 시퀀스 유형 확장 용이.
- 단점: 초기 도입 비용/배선 증가.
- 위험: 매니저 간 정합성/이벤트 순서 복잡성.

## 검증/메트릭
- 순서 감사: `ObligationManager.audit_order_all(where)`에 seq_id 축 추가, `(page, deadline)` 단조성 확인.
- 거절 로그: `RejectEvent.reason="sequence_guard"` 카운팅으로 가드 효과 확인.
- 성공률: 연속 의무의 in-time 달성률, 헤드 만료율, 의무/정책 수락률 변화.

## 테스트 전략
- 성공 경로: 연속 READ 세트가 head→tail 순서로 스케줄되고 중간 정책 제안이 순서 위배를 유발하지 않음.
- 실패 경로: head가 기한 초과 시 후속 아이템 정리, 정책 제안 수락(guard 통과)으로 정상 진행.
- 회귀: `sequence.enable=false`/`p_create=0`에서 기존 동작과 동일해야 함.

## 단계별 구현 가이드(권장 순서)
1) 데이터 모델/힙 스킵 로직(비-head 스킵) 추가 → 안전한 no-op 경계.
2) Guard만 우선 도입 → 순서 위배 방지 확인.
3) 제너레이터 `read_pages_linear`(READ page+1) 추가 → 의무 힙 등록/소비 확인.
4) CFG 튜닝(guard_window_us, p_create, length/step_gap) → 수락률/지연 최적화.

## 롤백/플래그
- `CFG["sequence"].enable`로 전역 온/오프.
- `p_create=0`으로 연속 생성 비활성화.
- Guard만 유지하여 순서 안전성 우선 확보 가능.

---

## 요약
- 연속 동작을 시퀀스 의무 세트로 모델링하고, 의무 우선과 head-only pop으로 순서를 보장한다.
- phase_conditional에서는 RNG로 단일/연속을 선택하고, 단일 수락 전에는 sequence guard로 위배를 차단한다.
- 최소 변경안으로 빠르게 도입 가능하며, 필요시 SequenceManager로 구조 확장할 수 있다.
- CFG 포맷: 포맷 B(Generated) 채택, 제너레이터 레지스트리 기반으로 평탄화/스케줄.
