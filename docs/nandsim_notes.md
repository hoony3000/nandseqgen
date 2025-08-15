# nandsim_demo.py 분석 노트

`nandsim_demo.py`는 NAND 플래시 메모리의 작업 순차열(Operation Sequence)을 생성하는 단일 파일 시뮬레이터입니다. 주요 특징은 이벤트 점프(Event-Jump) 방식의 스케줄러, 동적 정책 결정 엔진, 그리고 작업 간의 의무(Obligation) 관계를 모델링하는 기능입니다.

## 1. 전체 아키텍처

이 시뮬레이터는 **이산 사건 시뮬레이션(Discrete-Event Simulation)** 모델을 기반으로 합니다. 중앙의 이벤트 큐(우선순위 힙)를 사용하여 시간 순서대로 이벤트를 처리하며, 시뮬레이션 시간은 다음 이벤트가 발생할 때까지 "점프"합니다. 이 방식은 유휴 시간을 건너뛰므로 효율적입니다.

핵심 구성 요소들은 다음과 같이 상호작용합니다.

-   **Scheduler**: 이벤트 루프를 실행하며 전체 시뮬레이션의 흐름을 제어합니다.
-   **Policy Engine (SPE)**: 현재 시스템 상태와 설정된 가중치에 따라 다음에 실행할 작업을 제안합니다.
-   **Address Manager**: NAND의 주소 공간과 각 Plane의 상태(가용 시간, 프로그래밍된 페이지 등)를 관리합니다.
-   **Obligation Manager**: 특정 작업(예: `READ`)이 완료된 후 반드시 수행되어야 하는 다른 작업(예: `DOUT`)을 관리합니다.
-   **Configuration (CFG)**: 시뮬레이션의 모든 동작(타이밍, 가중치, 토폴로지 등)을 정의하는 중앙 설정 객체입니다.

## 2. 주요 구성 요소 (Key Components)

### `Scheduler`
-   **역할**: 이벤트 관리 및 실행의 중심.
-   **동작**:
    -   `self.ev`: 이벤트가 저장되는 최소 힙(min-heap). `(시간, 시퀀스, 타입, 페이로드)` 튜플을 저장하여 항상 가장 이른 시간의 이벤트가 먼저 처리되도록 합니다.
    -   `run_until()`: 지정된 시간까지 이벤트 루프를 실행합니다.
    -   `_schedule_operation()`: `PolicyEngine`이 제안한 작업을 실제 타임라인에 배치하고, 관련된 `OP_START`, `OP_END`, `PHASE_HOOK` 이벤트를 생성하여 힙에 추가합니다.

### `PolicyEngine`
-   **역할**: "어떤 작업을 다음에 할 것인가?"를 결정하는 두뇌.
-   **동작**:
    -   `propose()`: `PHASE_HOOK`이 트리거될 때 호출됩니다.
    1.  **의무(Obligation) 확인**: `ObligationManager`에 마감이 임박한 작업이 있는지 확인하고, 있다면 최우선으로 제안합니다.
    2.  **점수 계산 및 샘플링**: 의무가 없다면, `CFG`에 정의된 가중치(`base`, `g_state`, `g_local`, `g_phase`)와 현재 시스템 상태를 바탕으로 가능한 작업(`READ`, `PROGRAM`, `ERASE`)들의 점수를 계산합니다.
    3.  **가중치 기반 랜덤 선택**: 계산된 점수를 확률 분포로 사용하여 다음 작업을 랜덤하게 선택하고 제안합니다.

### `AddressManager`
-   **역할**: NAND 메모리의 상태를 추상화하고 관리합니다. (데모 버전에서는 매우 단순화됨)
-   **동작**:
    -   `available`: 각 Plane이 다음 작업을 시작할 수 있는 가장 빠른 시간을 추적합니다.
    -   `select()`: `PolicyEngine`이 작업 종류를 결정하면, 해당 작업을 수행할 구체적인 주소(블록, 페이지)를 선택합니다.
    -   `commit()`: 작업이 완료(`OP_END`)되면 NAND의 논리적 상태를 업데이트합니다. (예: `PROGRAM` 완료 시 해당 페이지를 '프로그래밍됨'으로 표시)
    -   `observe_states()`: 현재 상태를 `PolicyEngine`이 사용할 수 있는 "버킷"(예: `low`, `mid`, `high`)으로 변환하여 제공합니다.

### `ObligationManager`
-   **역할**: 작업 간의 시간적 종속성을 관리합니다.
-   **동작**:
    -   `on_commit()`: `READ`와 같이 의무를 발생시키는 작업이 완료되면, `CFG`의 `obligations` 규칙에 따라 `DOUT` 같은 후속 작업을 특정 시간(`deadline_us`) 내에 수행해야 한다는 의무를 생성하고 힙에 추가합니다.
    -   `pop_urgent()`: `PolicyEngine`이 다음 작업을 제안할 때, 마감이 임박한 의무가 있는지 확인하기 위해 호출됩니다.

## 3. `run_until` 실행 워크플로우

`main()` 함수에서 `sch.run_until(run_until_us)`이 호출될 때의 이벤트 처리 흐름은 다음과 같습니다.

1.  **초기화**:
    -   `Scheduler`가 생성될 때, `t=0`에 `QUEUE_REFILL` 이벤트와 각 Plane에 대한 `PHASE_HOOK` 이벤트가 미리 예약됩니다.

2.  **이벤트 루프 시작**:
    -   `Scheduler`는 이벤트 힙(`self.ev`)에서 가장 빠른 시간의 이벤트를 꺼냅니다.
    -   시뮬레이션의 현재 시간(`self.now`)이 해당 이벤트의 시간으로 "점프"합니다.

3.  **이벤트 유형에 따른 처리**:

    -   **`PHASE_HOOK` 처리 (가장 핵심적인 이벤트)**:
        1.  `AddressManager`를 통해 현재 Plane의 전역/지역 상태(`global_state`, `local_state`)를 관찰합니다.
        2.  `PolicyEngine.propose()`를 호출하여 다음 작업을 제안받습니다.
            -   (a) **의무(Obligation) 우선**: `ObligationManager`에 긴급한 작업(예: `DOUT`)이 있으면 해당 작업을 생성하여 반환합니다.
            -   (b) **정책 기반 제안**: 의무가 없으면, 상태와 가중치를 기반으로 `READ`, `PROGRAM`, `ERASE` 중 하나를 확률적으로 선택하고, `AddressManager`에서 대상 주소를 할당받아 `Operation` 객체를 생성하여 반환합니다.
        3.  제안된 `Operation`이 있으면 `Scheduler._schedule_operation()`을 호출합니다.
            -   Plane의 가용 시간을 고려하여 작업의 절대 시작/종료 시간을 계산합니다.
            -   `OP_START`와 `OP_END` 이벤트를 힙에 추가합니다.
            -   `make_phase_hooks()`를 호출하여 지금 스케줄링된 작업의 중간 단계들(예: `CORE_BUSY.START`, `DATA_OUT.END`)에 대한 새로운 `PHASE_HOOK`들을 생성하고 힙에 추가합니다. **이것이 시뮬레이션이 계속해서 새로운 작업을 생성하게 만드는 원동력입니다.**

    -   **`OP_START` 처리**:
        1.  콘솔에 작업 시작을 알리는 로그를 출력합니다. (실제 하드웨어의 Busy 시작에 해당)

    -   **`OP_END` 처리**:
        1.  콘솔에 작업 완료 로그를 출력합니다.
        2.  `AddressManager.commit()`을 호출하여 NAND의 논리적 상태를 변경합니다. (예: 페이지가 프로그래밍되었음을 기록)
        3.  `ObligationManager.on_commit()`을 호출하여, 이 작업이 다른 작업을 유발하는 경우(예: `READ` → `DOUT` 의무 생성) 새로운 의무를 등록합니다.

    -   **`QUEUE_REFILL` 처리**:
        1.  시스템이 유휴 상태에 빠져 더 이상 `PHASE_HOOK`이 생성되지 않는 것을 방지하기 위한 안전장치입니다.
        2.  주기적으로 각 Plane에 "Nudge" 성격의 `PHASE_HOOK`을 강제로 생성하여 `PolicyEngine`이 새 작업을 제안할 기회를 줍니다.
        3.  다음 `QUEUE_REFILL` 이벤트를 힙에 추가합니다.

4.  **반복**:
    -   지정된 `t_end` 시간에 도달하거나 이벤트 힙이 빌 때까지 2-3번 과정을 반복합니다.

이러한 구조를 통해 시뮬레이터는 과거 작업의 완료 시점, 현재 NAND의 상태, 그리고 작업 간의 종속성을 모두 고려하여 동적으로 다음 작업 순차열을 생성해 나갑니다.

## 4. 상태 관찰 및 업데이트 전략 (State Observation & Update Strategy)

`PolicyEngine`이 정확한 확률 분포에 따라 다음 작업을 제안하려면, 제안 시점(`now`)의 `global_state`와 `local_state`가 최신 상태를 정확히 반영해야 합니다. 현재 `nandsim_demo.py`의 `AddressManager.observe_states()`는 스텁(stub)으로 구현되어 있어 이 부분을 보강해야 합니다.

### 4.1. 관찰해야 할 핵심 상태

-   **전역 상태 (Global State)**: 디바이스 전체에 영향을 미치는 지표.
    -   `pgmable_ratio`: 전체 블록 중 프로그래밍 가능한(Erased 상태) 블록의 비율.
    -   `readable_ratio`: 프로그래밍이 완료되어 읽을 수 있는 페이지/블록의 비율.
    -   `gc_pressure`: `pgmable_ratio`가 특정 임계값(low watermark) 이하로 떨어졌는지 여부.
-   **지역 상태 (Local State)**: 특정 Plane에 국한된 지표.
    -   `plane_busy_frac`: 최근 일정 시간 동안 해당 Plane이 Busy 상태였던 시간의 비율.
    -   **`current_op_phase`**: **(핵심)** 현재 Plane에서 실행 중인 작업의 정확한 내부 단계(Phase). 예를 들어, `READ` 작업의 `CORE_BUSY` 상태 중간인지, `DATA_OUT` 상태의 끝부분인지 등을 식별해야 합니다.

### 4.2. 상태 업데이트 및 관찰 구현 전략

상태 정보의 **Source of Truth는 `AddressManager`**가 되어야 하며, **관찰 시점은 `Scheduler`의 현재 시간(`now`)**이 기준이 됩니다.

1.  **`AddressManager` 확장**:
    -   **실제 주소 상태 관리**: `self.programmed`와 같은 단순한 집합 대신, 전체 블록의 상태(`ERASED`, `PGM_LAST(page_idx)` 등)를 추적하는 `addrstates` 배열을 도입합니다.
    -   **Plane 타임라인 도입**: 각 Plane의 작업 이력을 저장하기 위해 `plane_timelines: Dict[Tuple[int, int], List[BusySlot]]` 속성을 추가합니다. `BusySlot`은 `{start_us, end_us, op}` 정보를 가집니다.

2.  **`Scheduler`와 `AddressManager` 연동**:
    -   `Scheduler._schedule_operation()`에서 작업의 절대 시작/종료 시간(`start`, `end`)을 계산한 후, `self.addr.reserve()` 호출 시 이 정보를 `BusySlot` 객체로 만들어 해당 Plane의 타임라인에 추가하도록 `AddressManager`의 인터페이스를 수정합니다.
        -   예: `addr.reserve(die, plane, start, end, op)`

3.  **`AddressManager.observe_states(die, plane, now)` 상세 구현**:
    -   이 함수가 호출되면, `now` 시점을 기준으로 다음을 계산합니다.
    -   **전역 상태 계산**: `self.addrstates` 배열을 스캔하여 `pgmable_ratio` 등을 실시간으로 계산합니다.
    -   **지역 상태 계산**:
        -   `plane_busy_frac`: `self.plane_timelines[(die, plane)]`에서 최근 시간 창(예: `now - 1000us` ~ `now`) 내의 `BusySlot`들을 분석하여 Busy 비율을 계산합니다.
        -   **`current_op_phase` 식별**:
            1.  `self.plane_timelines[(die, plane)]`에서 `slot.start_us <= now < slot.end_us`를 만족하는 현재 `BusySlot`을 찾습니다.
            2.  `BusySlot`을 찾았다면, `op = slot.op`를 얻습니다.
            3.  `op`의 시작 시간(`slot.start_us`)부터 `now`까지의 상대 시간(`rel_time = now - slot.start_us`)을 계산합니다.
            4.  `op.states` (`StateSeg` 리스트)를 순회하며 누적 시간을 계산하여 `rel_time`이 어떤 `StateSeg` 구간에 속하는지 찾아냅니다.
            5.  찾아낸 `StateSeg`의 이름(예: `CORE_BUSY`)과 해당 구간 내에서의 진행률(예: 40%)을 지역 상태에 포함시킵니다.
    -   계산된 값들을 `low`, `mid`, `high` 같은 버킷으로 변환하여 `PolicyEngine`에 전달합니다.

이 전략을 통해 `PHASE_HOOK`이 발생하여 `SPE.propose()`가 호출되는 모든 시점에서, 시스템은 현재 실행 중인 작업의 정확한 내부 단계까지 고려한 최신 상태 정보를 바탕으로 다음 작업을 제안할 수 있게 됩니다. 이는 상태에 따른 확률 분포의 정확성을 크게 향상시킵니다.

## 5. nandsim_demo.py v2: 고급 아키텍처 및 기능

최신 `nandsim_demo.py`는 초기 버전에 비해 훨씬 정교하고 선언적인 시뮬레이션 프레임워크로 발전했습니다. 핵심 로직 대부분이 `CFG`라는 중앙 설정 객체를 통해 제어되며, 다음과 같은 고급 기능들이 추가되었습니다.

### 5.1. Phase-Conditional Policy와 Admission Gating

-   **Phase-Conditional Policy (`phase_conditional`)**: 작업 제안의 핵심 로직입니다. 더 이상 단순 가중치 기반 스코어링에 의존하지 않고, 현재 실행 중인 작업의 특정 단계(`op.state.pos`, 예: `READ.CORE_BUSY.START`)를 조건으로 하여 다음에 시도할 작업들의 확률 분포를 직접 정의합니다. 이를 통해 매우 구체적이고 현실적인 시나리오(예: "READ의 CORE_BUSY 중에는 다른 READ나 SR만 시도")를 모델링할 수 있습니다.
-   **Admission Gating (`admission`)**: `PolicyEngine`이 제안한 작업을 즉시 스케줄링하지 않고, `now + delta_us`라는 짧은 미래의 시간 창 내에 시작할 수 있을 때만 수락하는 게이트 역할을 합니다. 이는 불필요한 탐색을 줄이고 시뮬레이션 성능을 향상시킵니다.

### 5.2. 동적 제약 및 제외 규칙 (Constraints & ExclusionManager)

-   **`constraints`**: `CFG`에 선언된 규칙 기반 제약 시스템입니다. 특정 작업(예: `PROGRAM`)의 특정 상태(`CORE_BUSY`)가 활성화될 때, 다른 종류의 작업들(예: `READ`, `ERASE`)을 특정 범위(`DIE`, `GLOBAL`)에서 일시적으로 금지하는 복잡한 하드웨어 규칙을 모델링합니다.
-   **`ExclusionManager`**: `constraints` 규칙을 해석하여 시뮬레이션 타임라인에 "제외 기간(Exclusion Window)"을 등록하고, `PolicyEngine`이 새 작업을 제안할 때마다 해당 작업이 금지된 기간에 속하는지 여부를 검사하여 제안을 거부하는 역할을 합니다.

### 5.3. AddressManager v2 및 Multi-Plane/Die-Wide 작업

-   **Committed/Future 상태 분리**: `AddressManager`는 주소의 상태를 `addr_state_committed` (이미 완료된 작업 기준)와 `addr_state_future` (예약은 되었지만 아직 완료되지 않은 작업 기준)로 나누어 관리합니다. 이를 통해 `READ`는 `committed` 데이터를 읽고, `PROGRAM`은 `future` 상태를 기반으로 계획을 세우는 등 더 정확한 상태 추적이 가능해졌습니다.
-   **Multi-Plane 계획 (`plan_multipane`)**: 단일 Plane 작업뿐만 아니라, 여러 Plane에 걸친 작업(Multi-Plane Operation)을 계획하고 실행합니다. `op_specs`에 정의된 `scope` (`PLANE_SET`, `DIE_WIDE`)에 따라 작업의 영향 범위를 결정합니다.
-   **작업 별칭 (`OP_ALIAS`)**: 사용자가 정책을 쉽게 정의할 수 있도록 `SIN_READ` (Single-plane Read), `MUL_READ` (Multi-plane Read)와 같은 별칭을 사용합니다. `PolicyEngine`은 이 별칭을 해석하여 적절한 fanout으로 `plan_multipane`을 호출합니다.

### 5.4. 자동 검증 및 시각화

-   `main` 함수는 시뮬레이션 실행 후 `viz_tools`를 사용하여 다양한 결과를 시각화하고(Gantt 차트, 3D 작업 순서도), `validate_timeline` 함수를 호출하여 생성된 타임라인이 `CFG`에 정의된 모든 타이밍 및 제약 조건들을 준수하는지 자동으로 검증합니다. 이는 시퀀스 생성 로직의 정확성을 보장하는 중요한 안전장치입니다.
