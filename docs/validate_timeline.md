## validate_timeline 검증 가이드

이 문서는 [viz_tools.py](mdc:viz_tools.py)의 `validate_timeline(df, cfg)` 함수가 수행하는 검증 항목과 동작 방식을 설명합니다. 시뮬레이터가 생성한 타임라인이 NAND 제약을 준수하는지 사후 검증할 때 사용합니다.

### 입력/출력
- **입력**
  - `df`(pandas.DataFrame): [TimelineLogger](mdc:viz_tools.py)이 생성한 타임라인 데이터프레임
    - 예상 컬럼: `start_us, end_us, die, plane, block, page, kind, base_kind, source, ...`
  - `cfg`(dict): 오퍼레이션 스펙과 정책이 담긴 설정. 최소한 `cfg["op_specs"]`에 각 base kind의 상태 시퀀스가 필요합니다.
    - 검증기는 고정 duration만 지원합니다. `op_specs[*].states[*].dist.kind == "fixed"`가 아니면 예외를 발생시킵니다.
- **출력**
  - `{"issues": List[ValidationIssue], "counts": Dict[str, int]}`
    - `issues`: 위반 목록(종류, 위치, 시간 구간, 상세 메시지)
    - `counts`: 위반 종류별 개수 집계

### 검증 항목(무엇을 체크하나요?)
`validate_timeline`은 아래 7가지를 확인합니다.

1) **READ_BEFORE_PROGRAM**
   - 동일 `(die, block, page)`에 대해 PROGRAM 커밋 이전에 READ가 시작되면 위반입니다.
   - 내부 상태 `state[(die,block)].committed`를 이용해 READ 시작 시점에 커밋 여부를 확인합니다.

2) **PROGRAM_DUPLICATE**
   - ERASE 없이 이미 커밋된 `(die, block, page)`에 다시 PROGRAM이 시작되면 위반입니다.

3) **PROGRAM_ORDER**
   - `(die, block)` 내에서 PROGRAM은 페이지 순서대로 진행해야 합니다. 기대 페이지는 `last+1`이며, 불일치 시 위반입니다.

4) **DOUT_OVERLAP**
   - DOUT는 전 구간에서 글로벌 프리즈로 동작합니다. DOUT 구간과 다른 모든 오퍼레이션의 시간 겹침이 존재하면 위반입니다.

5) **CORE_BUSY_DIEWIDE_OVERLAP**
   - PROGRAM/ERASE의 CORE_BUSY 윈도우 동안, 같은 die에서 READ/PROGRAM/ERASE가 어떤 형태로든 겹치면 위반입니다.

6) **MUL_READ_CORE_BUSY_OVERLAP**
   - MUL_READ의 CORE_BUSY 윈도우 동안, 같은 die에서 READ/PROGRAM/ERASE가 겹치면 위반입니다.

7) **SIN_READ_CORE_BUSY_OVERLAP**
   - SIN_READ의 CORE_BUSY 윈도우 동안, 같은 die에서 MUL_READ/PROGRAM/ERASE가 겹치면 위반입니다. (SIN_READ는 허용)

### CORE_BUSY/DOUT 윈도우 정의
- 함수는 `cfg["op_specs"]`로부터 각 base kind(READ/PROGRAM/ERASE/DOUT/...)의 상태 시퀀스를 해석해 고정 오프셋을 계산합니다.
  - 내부 헬퍼 `_spec_offsets_fixed(op_specs)`가 각 오퍼레이션에 대해
    - `total`: 전체 구간 (0 → total)
    - `core_busy`: CORE_BUSY 세그먼트의 상대적 구간 `(t0, t1)`
    를 산출합니다. 모든 세그먼트의 duration은 `dist.kind == "fixed"`여야 합니다.
- DOUT은 전체 구간이 글로벌 프리즈 윈도우로 간주되어, 어떤 오퍼레이션과도 겹치면 위반으로 기록됩니다.

### 동작 방식(알고리즘 개요)
1. 타임라인의 각 행에 대해 `(start, end)` 이벤트를 생성한 뒤 시간 순으로 정렬합니다. 같은 시간대에서는 `end`가 `start`보다 먼저 처리됩니다.
2. 이벤트를 순회하며 `(die, block)`별 상태를 갱신합니다.
   - `start` 시점: READ/PROGRAM 규칙(1~3)을 검사합니다.
   - `end` 시점: PROGRAM 커밋, ERASE 초기화 등을 반영합니다.
3. CORE_BUSY/DOUT 윈도우를 계산한 뒤, 같은 die(또는 글로벌)에서 시간 겹침이 있는지 검사합니다(4~7).

### 사용 예시
```python
import pandas as pd
from viz_tools import validate_timeline, print_validation_report
from nandsim_demo import CFG  # 데모의 고정 스펙/정책을 재사용

df = pd.read_csv("nand_timeline.csv")
report = validate_timeline(df, CFG)
print_validation_report(report, max_rows=30)
```

### 위반 감소 팁
- `cfg["constraints"]["exclusions"]`에서 PROGRAM/ERASE CORE_BUSY 중 die-wide 차단을 강화하거나, 정책 엔진의 제안 시기를 보수적으로 조정합니다.
- `cfg["phase_conditional"]`에서 MUL_READ 확률을 낮추거나, `cfg["selection"]["defaults"]["READ"]["fanout"]`을 1로 낮춰 SIN_READ 위주로 운영합니다.
- 특정 페이즈 키(예: `READ.CORE_BUSY.START`)에 대해 `selection.phase_overrides`로 팬아웃/인터리브를 제한할 수 있습니다.

### 관련 소스
- 검증 로직: [viz_tools.py](mdc:viz_tools.py) 내 `validate_timeline`
- 타임라인 로거/시각화/리포트: [viz_tools.py](mdc:viz_tools.py)


