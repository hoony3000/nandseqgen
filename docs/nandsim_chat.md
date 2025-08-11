---
title: ChatGPT Export
exported_at: 2025-08-11_14-46
---

## 👤 User

아래 글은 NAND operation sequence generation 을 위해 draft 로 작성한 design 문서야. 이 문서를 작성한 목적은 파이썬 코드 구현을 위해 system 설계를 어떻게 해야할 지 검토하기 위해서 작성했어. 이 문서의 표현을 가다듬고 구조화해서, 실제 구현에 도움이 되게끔, 그리고 구현 상 발생하는 risk 를 명시해서 그걸 최소화 하려고 해. 아래 문서를 위의 배경에 맞게 재작성 해줘.
————
**Symbols : 설명이 필요한 부분**
-. StateSeq : times, states 를 저장하여 시간대별 연속적인 nand state 를 저장하는 data class
-. Operation : Operation data class. StateSeq 를 포함함
-. OperSeq : Operation instance 의 배열
-. HostReq : host 의 요청 data class. 이것을 해석하여 OperSeq 생성
-. scheduler
    -. 기능 : nand 동작을 실제로 스케쥴링 해서 긴 nand 동작 seq 를 생성
    -. busytable : Operation 을 등록하면 시간대별 연속적인 nand state 가 저장됨.
    -. historylist : Operation 을 등록하면 시간대별 Operation 의 점유율 저장
    -. timer : Operation 이 실제 수행됐을 때, 동작이 끝날때까지 남은 시간 저장. clock 이 앞으로 움직이면 차감됨.
-. Address manager
    -. 기능 : nand 의 erase, pgm, read 를 수행하기 위해 block 이 어떤 상태인지 확인하고, 요청이 오면 동작 가능한 address 를 리턴.
    -. addrstates : address 에서 nand 의 pgm 된 상태를 나타냄
    -. -3 : badblock, can’t be used
    -. -2 : initial, not erased
    -. -1 : ERASED
    -. 0~pagesize-1 : 마지막 pgm 된 page address 값
    -. get_adds_val : address 에 저장된 addrstates 값을 반환.
    -. get_pgmable : nand 전체 address 에서 pgm 가능한 ddress 반환
    -. get_readable : nand 전체 address 에서 read 가능한 address 반환
-. Queue mananger
    -. 기능 : queue list 를 우선 순위에 맞춰 정렬 및 queue 의 값을 반환
    -. queue list : host 또는 nand 가 요청한 Operation 을 저장하는 data type
    -. dependency check : nand 는 target address 에 대해서 erase -> pgm -> read 순으로 진행되어야 하고, Operation sequence 가 그 규칙에 맞는지 확인하는 기능

**Operation 을 NAND busytable 에 추가하는 순서**
1. scheduler 가 Queue manager 에게 요청 사항 list 가 남아 있는지 확인
2. Queue manager 는 우선 순위를 업데이트 하고, 첫번째 HostReq 를 scheduler 에게 전달
3. scheduler 는 HostReq 를 HostReq to OperSeq 에 인자로 넘겨서 OperSeq 를 반환 받음
4. OperSeq 는 scheduler 내부 queue list 에 (Oper1, until_time1), (Oper2, until_time2), … 에 저장해두고, Operation 은 busytable, historylist 에 추가. until_time 은 timer 에 할당하고 clock 이 움직이는 거에 따라 차감하고, until_time1 이 0 이 되면 scheduler 는 Operation2 를 추가하고, until_time2 를 timer 에 할당한다.
5. until_time 이 계산되기 위해서 StateSeq 나, Operation class 에서 times 배열을 보고 until_time 을 계산해야 할 듯
6. scheduler 는 HostReq 로 요청한 Operation : ophost 과, scheduler 자체적으로 샘플링한 Operation : opnand 중 선택해야 한다.

**self thoughts**
-. 어느 시점에 req_queue 를 들여다 봐야 하는가?
-. sampling 은 어느 시점에 host, nand 중 어디에서 해야 하는가?
-. host 요청이 무한히 쌓이는 경우 nand 의 동작을 수행할 수 없기 때문에, host 의 요청을 적절히 제한하는 메커니즘 필요. queue list 의 크기를 제한하면 가능. queue list 를 들여다 보는 주체는 scheduler
-. host 가 요청한 operation 과 nand 가 자체 수행하는 operation 은 우선 순위가 존재. host 의 요청이 최우선이다. 현재 수행되는 operation 이 host 의 요청인지 구분할 수 있는 데이터나 구조 필요-. host 의 명령에 의한 operation 도 우선순위 존재. pgm < read 이며, pgm 을 수행하려고 했지만, nand address manager 가 pgm 할 block 이 없다고 하면, read (to assign a new block) > dout > erase > pgm (new) > pgm (target) 순으로 수행해야 한다. (엄청난 latency 발생). 이런 이유로 nand 가 현재 pgmable block 이 어느 정도 있는지 tracking 하면서 garbage collection 용 operation 을 추가해야 함.
-. nand 가 자체적으로 수행하는 OperSeq 는
-. Garbage Collection : pgm 가능한 block 을 만듦
    -. invalid ratio in a block 을 모니터링 해서 수행
    -. read > dout > pgm (new) > read > dout > pgm (new) > … > erase
-. Read Reclaim : 오래된 block 을 읽어보며 failbit 수준 모니터링
    -. read > dout > read > dout > … > read dout
-. Garbage Collection, Read Reclaim 을 추가할 것인지는 option 으로 관리
-.결국 host, nand 모두 생성해야 할 data class 는 OperSeq 이고, Host 의 요청에 대응하는 것은 nand 가 랜덤한 target address 에 할당하면 되고, nand 는 어떤 address 에 어떤 동작을 했다는 것을 참고한 target address 를 생성하는게 자연스럽다. 하지만 그것을 고려한 target address 샘플링 로직을 넣진 않겠다.
-. nand 가 Operation 을 추가할 때, 바로 확인 가능한 동작(SR, reset 등) 은 바로 추가하면 되고, 그렇지 않은 동작은 지금 수행되는 동작을 기다리든지(만약 step clock sampling 이라면), 아니면 끝나는 시점을 계산해서 그 때 추가하게 한다. 이 때 die, plane 의 busytable 을 모두 참고해서 가장 빠른 시점을 기준으로 한 time 을 샘플링 하여 추가.
-. 동작 예약은 plane 당 하나만 busytable 에 예약 가능하며 미래 시점을 샘플링 해서 예약. 예약 시 queue list 에 넣어두고, 실행은 현재 시간 기준으로 한다. 실행의 의미는 예약된 동작이 queue list 에서 빠지기 때문에 다음 동작 예약이 가능해 진다. 실행 시간은 step clock 단위로 하는 게 아니라 예약된 동작이 시작되는 시점으로 clock 을 옮겨서 실행. 그래야 seq vs. patt 간 mismatch 가 없다.
-. queue list 에 있는 동작들 사이에서는 순서상 dependency check 을 만족해야 한다. 여기서 host, nand 간 우선순위 충돌이 일어나는 부분인데, 구체적 예는 다음과 같다
-. host 는 read 요청을 했고, nand 는 해당 block 을 erase 하고, 다른 block 을 pgm 하려고 한다 : read 요청을 queue list 에서 우선순위로 해서 sorting 을 하고, erase 동작 이후의 동작은 나중으로 한다. 이 경우 nand 는 이미 address manager 로 해당 block 을 ERASE 상태로 변경했기 때문에, 해당 block address 에 접근하지 못하여 address manager 의 get_adds_val 값이 invalid 하게 나온다. (scheduler 가 host 의 read 요청을 처리할 때 get_adds_val 값과 target address 를 비교하여 우선 순위 sorting trigger 를 발동 시켜야함).
-. host 의 요청은 항상 address manager 의 컨펌을 받고, dependency check 을 queue list 에 대해서 수행하고(list 의 가장 처음에 추가된다고 하면 dependency check 를 수행할 필요가 없다).
-. read operation 이 순차적으로 예약되어 있으면, 순서를 조정해 overall read latency 를 개선하기 위해서 concurrent active plane 이 최대가 되게끔 순서를 조정해야 한다. 만약 read 중간에 pgm, erase 가 껴있으면, read 를 앞에 최대한 배치를 한다. 배치 이후에는 dependency check 를 해서 문제가 없는지 확인이 필요하다. 그러기 위해서는 address manager 가 read 의 이동 전에 dependency check 를 우선 수행해야 하고, 만약 불가능하다고 하면 이동하지 않아야 한다. 만약 OperSeq 로 연속적인 operation 을 queue 에 추가할 때, 이것은 dependency 가 있는 Seq 이니 이동이 불가하다는 정보를 저장해 둔다면, 사전에 방지할 수 있다. 하지만, 별개로 추가한 operation 이 dependency 가 형성되어 버렸다면, 이를 사전에 알지 못하므로 dependency check 를 진행해야 한다. 또 하나 다른 문제는, address manager 가 validity check 를 시점을 옮겨 가며 해야 한다. 옮기기 전과, 옮긴 후에 addrstates 값이 다르기 때문에 그걸 다루는 로직이 필요하게 된다.

**Decisions**
-. system 의 역할을 정의 필요
    -. nand 의 address manager 를 통해 get_pgmable(), get_readable() 을 수행하며, 전체 address 에서 pamgable, readable 가 충분하도록 비율을 조절해야 한다.
    -. host 요청에 의한 HostReq 를 OperSeq 로 변환 후 scheduler 에 추가
-. Garbage Collection 
    -. 목적 : pgm block 가능한 block 을 만드는 것으로 한정
    -. system 이 주기적으로 address manager 에게 get_pgmable() 을 전달받아서 Erase 용 OperSeq 를 여러개 추가

**Critial Questions**
-. Clock 은 어떤 step 으로 움직여야 하는가? step 형식 or likelyhood sampling?
-. die 샘플링은 어떠해야 하는가?
-. plane interleave 를 적극적으로 하려면 어떻게 해야 하는가?
-. host 와 nand 의 요청 동작 사이의 우선 순위는 어떤 식으로 설정, 또는 조정해야 하는가?

## 🤖 Assistant

NAND Operation Sequence Generation — 구현 지향 설계 리라이트 (v0.2)
1) 목적과 범위

목적: 이 설계는 NAND 동작 시퀀스를 파이썬 코드로 구현하기 위한 실무 지침이다. 구현에 필요한 데이터 모델, 스케줄링 방식, 우선순위 규칙, 의존성 검사, GC/Read-Reclaim 정책, 리스크와 완화책을 명료하게 정리한다.

비범위: 실제 물리 지연 상세치(베너, 셀 타입별 μs/ns 단위 파라미터)는 외부 테이블로 두고, 본 문서는 시뮬레이터의 메커니즘에 집중한다.

2) 시스템 개요
구성요소와 책임:

Scheduler: 이벤트 기반(Discrete-Event)으로 동작을 예약/실행하여 긴 NAND 시퀀스를 생성. BusyTable/History/Timer 관리.

AddressManager: 블록/페이지의 상태 추적, pgm/read 가능한 주소 질의 제공, 예약/커밋/롤백 지원.

QueueManager: Host/NAND(유지보수) 요청을 한데 모아 우선순위 정렬 및 디스패치.

HostReq → OperSeq 변환기: 호스트 요청을 실제 Operation 시퀀스(OperSeq) 로 변환.

StateSeq/Operation/OperSeq 데이터 모델: 시간축과 상태를 일관되게 기록/검증.

핵심 원칙

이벤트 점프형 시뮬레이션: step clock로 1틱씩 전진하지 않고, 다음 이벤트 시각으로 시간을 점프해 성능/정합성을 확보.

예약(Reservation) → 의존성/자원확정(Validation) → 커밋(Commit) 3단계 프로토콜로 경합/레이스 완화.

명시적 우선순위: Host > Read계열 > DOUT > Erase > Program(신규) > Program(타겟) 기본 정책 + 기아 방지.

3) 데이터 모델 (Python 스켈레톤)

```python
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Tuple, Optional, Dict, Any
import heapq
from collections import deque

class AddrState(Enum):
    BAD_BLOCK = auto()     # 사용불가
    INITIAL   = auto()     # 초기화 전(미소거)
    ERASED    = auto()     # 소거 완료
    PGM_LAST  = auto()     # 마지막 프로그램된 page 인덱스 보유

@dataclass(frozen=True)
class Address:
    die: int
    plane: int
    block: int
    page: Optional[int] = None

class OpKind(Enum):
    READ = auto()
    DOUT = auto()
    PROGRAM = auto()
    ERASE = auto()
    SR = auto()            # status read
    RESET = auto()

@dataclass
class StateSeq:
    times: List[int]          # 상대/절대 시각 배열
    states: List[str]         # "idle/busy/dma/..." 등 태그

@dataclass
class Operation:
    kind: OpKind
    targets: List[Address]             # 단일 또는 인터리브 대상
    timeline: StateSeq                 # 상대시간 단계 (issue->busy->dma...)
    movable: bool = True               # 재배치 허용 여부(의존성 있는 묶음은 False)
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class OperSeq:
    ops: List[Operation]

@dataclass
class HostReq:
    kind: OpKind
    size: int                           # 논리적 크기 / 페이지 수
    policy: Dict[str, Any] = field(default_factory=dict)  # QoS 등

@dataclass
class BusySlot:
    start: int
    end: int
    op: Operation

@dataclass
class PlaneState:
    available_at: int = 0               # 이 평면이 비는 시각
    timeline: List[BusySlot] = field(default_factory=list)

```

4) Scheduler 설계
4.1 이벤트 기반 루프

이벤트 힙(최소 힙): (event_time, event_type, payload) 형태로 관리.

현재 시각 now 를 다음 이벤트 시각으로 점프.

Timer/History 필요 시 Event로 모델링:

OP_START, OP_END, QUEUE_REFILL, GC_TICK, READ_RECLAIM_TICK 등.

4.2 BusyTable/History/Timer

BusyTable: die/plane 단위로 available_at + timeline(디버깅/리포트용).

HistoryList: 주기적으로 Utilization/큐 길이/우선순위별 대기시간을 샘플링.

Timer: 개별 관리 대신 이벤트 스케줄링 시각 자체가 타이머 역할.

4.3 Operation 추가 절차 (정제)

QueueManager 에게 다음 할당 요청

첫 요소(또는 룩어헤드 재배치 결과)를 HostReq→OperSeq 변환

Reservation: AddressManager에 대상 주소 잠금/예약 (TTL 포함)

Validation:

의존성(erase→pgm→read) 및 Plane Busy 충돌 검사

가능 시 각 plane의 start = max(now, available_at) 계산

Commit & Schedule:

BusySlot 확정, OP_START/OP_END 이벤트 push

Address 상태 변화는 END 시점에 커밋(중간 실패 대비)

Completion:

OP_END에서 AddressManager에 상태커밋, BusyTable/History 반영

후속 의존 Operation이 있다면 즉시 Reservation 시도

기존 “until_time”은 Operation.timeline의 상대시간 합으로부터 END 절대시각을 계산하여 이벤트로 관리한다.

5) AddressManager 설계
5.1 상태 표현

블록 단위 상태: BAD_BLOCK, INITIAL, ERASED, PGM_LAST(page_idx)

Enum + 구조체로 마이그레이션(기존 -3/-2/-1/0~pagesize-1 숫자치 지양)

조회 함수:

get_addr_state(addr) -> AddrState or (PGM_LAST, page_idx)

get_pgmable() -> Iterable[Address(block-level)]

get_readable() -> Iterable[Address(page-level)]

5.2 예약/확정/롤백

reserve(addr, op_kind, until_time, token) -> bool

commit(token) / rollback(token)

동일 주소에 대한 동시 예약 방지 및 TTL 만료 시 자동 롤백.

가비지 컬렉션과의 경합을 피하려면:

Host READ 대상 블록은 GC가 예약 불가(또는 우선순위 낮춤)

GC는 대체 가능 블록만 대상(유효비트/invalid ratio 기준)

6) QueueManager 설계
6.1 큐 구조

HostQueue (높은 우선순위), NandMaintQueue(GC/Read-Reclaim) (낮은 우선순위) 분리

Unified View: peek(n) 시 두 큐를 합쳐 정책함수로 정렬

6.2 우선순위 정책(기본)

Host 요청 우선

Host 내부: READ > DOUT > ERASE > PROGRAM(new) > PROGRAM(target)

유지보수(NAND 자체): 임계치 기반 가중

pgmable_ratio < low_watermark 이면 GC 점수↑

오래된 블록의 에이징/FailBit 위험 시 Read-Reclaim 점수↑

기아 방지: 대기시간에 비례 가중치 추가(에이징)

6.3 의존성 검사

큐 삽입 시 1차 검사, 스케줄 직전 2차 검사

movable=False 인 연속 OperSeq 묶음은 재배치 금지

재배치 하려면 사전 의존성 그래프로 사이클 없는지 확인

7) HostReq → OperSeq 변환

READ: READ(+optional DOUT) / 인터리브 가능한 다중 페치

PROGRAM: 대상 블록/페이지 할당(없으면 GC 트리거 또는 읽기→신규할당)

ERASE: 블록 단위

결과는 Operation 리스트(OperSeq) 로 반환하되,

서로 강하게 의존하는 묶음은 movable=False로 플래그

8) 재배치(리드 전진) & 인터리브

Look-ahead 윈도(예: 다음 K개)에서 READ를 앞당겨 plane 병렬성 최대화

재배치 전후에 AddressManager로 유효성/의존성 재검증

인터리브는 (die, plane) 가 다른 타겟을 동일 시각에 issue하여 BUSY 중첩 최댓화

9) GC & Read-Reclaim 정책
9.1 Garbage Collection

목적 한정: pgm 가능한 블록 확보

트리거: pgmable_ratio < LOW_WATERMARK 또는 주기적 점검 이벤트

시퀀스 예: read -> dout -> program(new) -> ... 반복 후 erase

옵션화: enable_gc: bool / 정책 파라미터 구성

9.2 Read-Reclaim

목적: 에이징/FailBit 위험 블록을 선제 복구/이전

트리거: 블록 ‘나이’, 에러 통계, 주기적 스캔

옵션화: enable_read_reclaim: bool

10) 구현 절차(이벤트 라이프사이클)

```
[Scheduler.tick()]
  ├─ now = pop(next_event_time)
  ├─ process(event)
  ├─ if QUEUE_REFILL or idle:
  │    ├─ req = QueueManager.peek_and_select()
  │    ├─ operseq = Req2Seq(req)
  │    ├─ for op in operseq:
  │    │    ├─ AddressManager.reserve(...)
  │    │    ├─ Dependency/Busy validation
  │    │    └─ schedule OP_START/OP_END (commit at END)
  └─ push next QUEUE_REFILL / GC_TICK / RECLAIM_TICK as needed

```

11) 구성/파라미터(예시 기본값)

LOOKAHEAD_K = 8 (재배치 대상)

LOW_WATERMARK_PGMABLE = 0.05, HIGH_WATERMARK_PGMABLE = 0.12

AGEING_WEIGHT_ALPHA = 0.01 (대기시간 가중)

RESERVATION_TTL = operation_duration * 1.5

MAX_QUEUE_SIZE = 10_000 (호스트 폭주 억제)

12) 관측/로깅

메트릭: 큐 길이, 우선순위별 대기시간, plane utilization, GC 빈도, 재배치 성공률, 롤백 횟수

트레이스: Operation 단위 예약→커밋→완료 타임라인

스냅샷: AddressState 압축 스냅샷(주기/사건 기반)

13) 리스크 & 완화책
리스크설명완화책
우선순위 역전Host READ가 GC에 막힘Reservation에서 Host 대상 블록은 GC 예약 불가. 에이징 가중치로 Host 먼저 통과
주소 정합성 붕괴GC가 먼저 ERASE 상태로 바꾸어 Host READ invalid커밋 지연(END에서만 AddressState 변경), Host 예약시 강한 락
의존성 깨짐(재배치)읽기 순서 당김 후 종속성 위반movable=False 묶음, 재배치 전후 더블체크
스타베이션유지보수 영원히 못 돌림Low watermark 하회 시 유지보수에 보너스 점수, 주기적 강제 슬롯
이벤트 드리프트step clock와 패턴 미스이벤트 점프 모델 채택, 상대시간→절대시간 변환 일원화
동시 예약 충돌여러 스레드/모듈이 동일 주소 예약AddressManager 토큰 기반 예약, TTL과 롤백
메모리 팽창History/Timeline 누적롤링 윈도우 유지, 스냅샷 압축 저장
성능 병목10만+ 전이 테이블이벤트 힙, O(log N) 스케줄, 재배치 윈도 제한

14) 테스트 전략(필수 시나리오)

단위: Address 상태 전이(ERASE→PGM→READ), 예약/커밋/롤백, 의존성 검사

통합: Host 폭주 + GC 동시 동작, 재배치 유효성, 인터리브 효율성

부하: 수십만 이벤트 처리 시간, 메모리 상한

회귀: 동일 Seed 재현성(난수 시드 고정)

15) 파일 구조 제안

```
nandsim/
  core/
    scheduler.py
    queue_manager.py
    address_manager.py
    models.py
    req2seq.py
  policy/
    priority.py
    gc.py
    reclaim.py
  sim/
    runner.py           # main loop + config
  tests/
    test_address.py
    test_depcheck.py
    test_scheduler.py
    test_gc_reclaim.py

```

16) 의사코드 스니펫
16.1 스케줄러 핵심

```python
class Scheduler:
    def __init__(self, qm, am, config):
        self.now = 0
        self.events = []  # heap
        self.qm = qm
        self.am = am
        self.config = config
        heapq.heappush(self.events, (0, "QUEUE_REFILL", None))

    def run_until(self, t_end):
        while self.events and self.events[0][0] <= t_end:
            self.now, ev, payload = heapq.heappop(self.events)
            self._handle(ev, payload)

    def _handle(self, ev, payload):
        if ev == "QUEUE_REFILL":
            self._dispatch()
            heapq.heappush(self.events, (self.now+ self.config.queue_refill_period, "QUEUE_REFILL", None))
        elif ev == "OP_START":
            pass
        elif ev == "OP_END":
            op, token = payload
            self.am.commit(token)
            # history/busy update...

    def _dispatch(self):
        req = self.qm.peek_and_select(self.now)
        if not req: return
        operseq = req2seq(req, self.am, self.config)
        for op in operseq.ops:
            token = self.am.reserve_targets(op.targets, op.kind, ttl=self._estimate_ttl(op))
            if not token:
                # could push back or trigger GC
                continue
            start, end = self._fit_to_planes(op)
            heapq.heappush(self.events, (start, "OP_START", op))
            heapq.heappush(self.events, (end,   "OP_END",   (op, token)))

```

17) “Self-thoughts” 항목 반영(정책화)

언제 req_queue를 볼까?

QUEUE_REFILL 주기 + idle 시 즉시 확인. 주기는 짧게(예: 1~5μs 가상시간), 과도 호출 방지.

샘플링은 어디서?

Scheduler가 트리거를 결정하고, HostReq/GC/Read-Reclaim 생성은 각 소스 모듈이 수행. 스케줄러는 다만 우선순위+의존성 기준으로 채택.

Host 폭주 제어

MAX_QUEUE_SIZE 초과 시 신규 HostReq 백프레셔(거절/지연), 또는 저우선 유지보수 슬롯 보장.

Host/NAND 우선순위 구분 데이터

Operation.meta["source"] in {"host","gc","reclaim"}

Program 가능 블록 부족 시 처리

즉시 GC 점수 상승 → GC OperSeq 주입 → READ/DOUT을 통한 신규 블록 할당 경로 허용, 단 Host READ는 항상 선행.

즉시 가능한 동작(SR/RESET)

같은 plane 비점유 구간이면 now에 바로 스케줄. 아니면 available_at에 맞춰 이벤트 등록.

예약은 plane당 1개 규칙

“미래 예약 1개”를 기본으로 하되, same-plane 병목이 심하면 ‘연속 슬롯 예약’ 옵션 고려.

READ 재배치로 레이턴시 최적화

Look-ahead 윈도 내 movable READ들을 plane 분산되게 전진 배치 후 더블체크.

Validity 체크 시점 문제

재배치 전/후 두 번 AddressManager에 질의. 상태가 바뀌면 재배치 취소. 스냅샷(Epoch) 비교로 시점 일관성 보장.

18) 결정사항 요약

이벤트 점프형 스케줄러 채택(“step clock” 미사용)

AddressManager 상태는 Enum/구조화, 예약-커밋-롤백 프로토콜 도입

우선순위 규칙 명시 + 기아 방지

GC 목적 한정(프로그래머블 블록 생성), low-watermark 기반 자동 삽입

READ 재배치/인터리브는 movable 플래그와 이중 검증 하에서만

19) 미해결 이슈 & 권고 기본값 (Critical Questions)

Clock 스텝?

권고: 이벤트 점프형(Discrete-Event). 상대타임라인을 절대시간으로 변환해 OP_END 기준으로 커밋.

Die/Plane 샘플링?

권고: 최소 종료시간 우선(earliest available plane) + 균형 라운드로빈. 대규모 시엔 히스토리 기반 부하균형.

Plane 인터리브 극대화?

권고: Look-ahead 재배치로 READ를 선행 배치, movable=False 묶음은 제외.

Host vs NAND 유지보수 우선순위 조정?

권고: Host 절대우선 + Low watermark 하회 시 유지보수에 보너스 가중. 장기적으로는 SLA 기반 정책 함수 주입 가능하게 설계.

20) 다음 단계(바로 코드로 이어가기)

models.py의 Enum/Dataclass 확정(숫자 상태 제거)

address_manager.py에 reserve/commit/rollback 1차 구현 + 단위테스트

priority.py에 우선순위 함수/에이징 가중 구현

scheduler.py에 이벤트 루프/힙/BusyTable 적용

req2seq.py에 Host READ/PGM/ERASE 기본 변환기

소규모 시나리오(단일 die/plane)로 회귀테스트 템플릿 작성 → 점진 확장

필요하면 위 스켈레톤을 바로 작동 가능한 최소 실행 코드로 이어 붙여 드릴게요. 구현하면서 막히는 지점(예: 예약 토큰 구조, 재배치 알고리즘 세부) 나오면 해당 부분만 떼서 구체 코드로 파고들어가자고요.

## 👤 User

제안한 시스템에서 관점을 좀 추가할거야.
-. 설계하려는 시스템은 nand operation sequence 생성이 목적이며, edge case 를 포함한 use case sequence 생성을 하는 것이다.
-. erase/pgm/read 뿐 아니라 다양한 operation 을 나오게 하기 위해 operation 의 확률 분포를 tuning 해서 생성할 수 있어야 한다. 이 때 operation 의 확률 분포는 nand 상태에 따라 달라지는 확률 분포이다. nand 상태는 operation 이 수행될때마다 동적으로 바뀐다.
-. 특정 operation 은 항상 쌍으로 interval 을 두고 등장한다. 예를 들어, read 와 dout.
-. 위 관점에서 다양한 timing 에서의 nand operation 이 추가돼야 한다. 예를 들어, operation busy 의 start, midle, end 지점 부근에 operation 이 수행되어야 한다. 추후에 이 timing 에 대한 확률 분포를 조정해서 tuning 된 확률 분포를 가지고 생성을 할 것이다. operation busy 기준 샘플링 시 die, plane 별로 서로 다른 phase 가 있을 수 있다.

이런 관점에서 평가할 요소는 다음과 같아.
-. host <-> nand 간 우선 순위 조정 메커니즘의 복잡성이 확률 분포 tuning 과 공존할 수 있는지? 그렇다면 그에 따른 구현 난이도, risk 는 무엇인지?
-. host <-> nand 가 별개의 주체로 요청을 하지 않는다면, 어느 쪽을 기준으로system 을 설계해야 하는지?

평가를 진행해줘.

## 🤖 Assistant

좋아, “확률 분포로 동작을 생성하는 시퀀스 제너레이터” 관점까지 넣어서 설계를 평가하고 개선안을 제시할게. 핵심은 **우선순위/의존성 규칙(현실성)**과 **확률 분포 튜닝(제어성)**을 충돌 없이 공존시키는 구조를 갖추는 거야.
1) 결론 먼저 — 어떤 구조가 좋은가?

권장 아키텍처: “단일 확률 정책 엔진(SPE, Stochastic Policy Engine) + 이벤트 스케줄러”

Host와 NAND(GC/Read-Reclaim 등)를 별도 프로듀서로 두지 말고, 하나의 정책 엔진에서 “트래픽 클래스(host, gc, reclaim, diag…)”를 혼합 비율로 샘플링.

각 Operation의 발생 확률은 π(op | 전역상태 S_t, 로컬상태 L_{die,plane}, phase)로 모델링(상태-의존 확률).

**쌍 연산(READ↔DOUT)**은 “의무(Obligation)” 객체로 강제(“A가 나오면 B가 Δt 분포 내 반드시 등장”).

타이밍 주입은 스케줄러가 만들어주는 **phase hook 이벤트(START±ε, MID±ε, END±ε)**에서 SPE를 호출해 샘플링.

스케줄러는 여전히 우선순위/의존성으로 충돌을 해소하되, SPE가 사전 가용성 체크를 해 거절/재샘플링을 줄임.

이 구조면 “우선순위 메커니즘”과 “확률 튜닝”이 서로 발을 밟지 않고 같이 굴러가며, 구현도 가장 깔끔하게 나눠져.

2) Host↔NAND 우선순위와 확률 튜닝의 공존성 평가
공존 가능? → 예

분리 원칙: “무엇을 ‘시도’할지”는 SPE가, “무엇을 ‘실행’할지”는 스케줄러가.

SPE: 상태-의존 확률로 후보다발 생성(시도). 쌍/의존성/타이밍 제약을 반영해 유효 후보만 내놓음.

Scheduler: Host > READ > … 우선순위, plane busy, 의존성 그래프를 최종 해석(실행).

분포 보존: 실행 거절·지연이 잦으면 분포가 틀어지므로, SPE가 가용성/의존성 사전검사(light) 후 내놓아 리젝션율↓.

피드백 루프: 실행 결과(accepted/denied, 대기시간)를 SPE에 피드백해 혼합비율·타이밍 가중을 미세조정(튜닝 루프).

구현 난이도 & 리스크

난이도(중~상):

SPE에 상태-의존 확률표(또는 간단한 파라메트릭 함수) + 쌍 연산 의무 관리(Obligation Manager) + phase hook 호출이 필요.

스케줄러는 기존 설계(이벤트 점프, 우선순위/의존성) 재사용.

주요 리스크와 완화

분포 드리프트(우선순위/자원 부족으로 의도한 분포와 실행 분포가 어긋남)  

→ SPE에 가용성 기반 보정(plane_free_ratio, pgmable_ratio) + 실행/거절 통계로 혼합비율 업데이트.

의무 미충족(READ 발생했는데 DOUT 기한 미도달)  

→ Obligation에 데드라인 분포와 보호 우선순위 부스팅(마감 임박 시 점수↑).

상태-의존 확률표 폭발(조합 많아짐)  

→ 전역상태 S_t, 로컬 L_{die,plane}를 버킷팅(low/med/high)해 조각난 테이블로 관리 + YAML로 외부화.

페이즈 주입-의존성 충돌  

→ phase hook에서 샘플 전 의존성 사전검사(AddressManager, dependency rules) → 불가 시 다른 후보로 재샘플.

3) “Host와 NAND를 별개 주체로 두지 않는다면?” 기준선 선택
기준 설계장점단점추천도
Host-중심(NAND 유지보수는 Host 트래픽의 부산물)현실의 I/O 워크로드 닮음GC/진단/엣지케이스 주입 제어가 약함△
NAND-중심(유지보수/내부정책이 1급, Host는 클래스 중 하나)GC/엣지케이스 주도권↑, 내부 타이밍 주입 쉬움워크로드 현실감은 조정 필요◎
단일 정책 엔진(혼합 클래스)튜닝·관측 일원화, 현실감↔엣지케이스 균형SPE 구현 필요(중난이도)최고

본 목적이 “엣지케이스 포함 시퀀스 생성”이므로, 단일 정책 엔진으로 가고 Host는 한 클래스로 취급하기를 권장.

4) 구체 설계 포인트
4.1 상태-의존 확률(예시)

전역: pgmable_ratio ∈ {low, mid, high}, readable_ratio, 큐길이, wear_level 등

로컬: plane_busy_frac, die_age 등

페이즈: {pre-start, near-start, mid, near-end, post}

정책:

```
π(op | S,L,phase, class) = base(op,class) 
                           × g_state(op,S) 
                           × g_local(op,L) 
                           × g_phase(op,phase)

```

전부 YAML 파라미터화 → 오프라인 튜닝 용이

4.2 쌍 연산 의무(READ↔DOUT 등)

Obligation: {need: DOUT, issuer: READ, window: Δt ~ Dist, targets, priority_boost(t)}

READ가 커밋되면 Obligation 생성 → phase hook/queue refill 시 우선 샘플 대상에 포함

마감 임박 시 priority_boost↑ 또는 강제 삽입 슬롯 부여

4.3 타이밍/페이즈 주입

스케줄러가 각 BUSY에 대해 hook 이벤트 발행:

t_start±ε, t_mid±ε, t_end±ε(die/plane별 상이한 위상 허용)

hook 처리 시 SPE 호출 → 후보 샘플 → 사전검사 → 스케줄러에 제출

분포 튜닝: g_phase(op,phase)를 조정해 특정 구간에서 op 빈도↑

4.4 실행 전 사전검사(리젝션 절감)

AddressManager에 가용성/락 확인

의존성 그래프(erase→pgm→read) 위반 여부

plane available_at 계산해 시작 가능시각 산출(불가면 재샘플)

5) 복잡도/리스크 비교 (요약)
항목두 프로듀서(Host+NAND)단일 정책 엔진(권장)
구현 복잡도상 (상호작용/드리프트 처리 2중)중~상(엔진 1개로 일원화)
분포 제어성낮음(우선순위에 자주 깨짐)높음(사전검사/피드백로프)
엣지케이스 주입제한적용이
현실성(워크로드 유사)좋음튜닝 필요(혼합비율로 커버)
리스크드리프트/기아의무미충족/파라미터 폭발(완화책 있음)

6) 바로 넣기 쉬운 인터페이스 초안
6.1 정책 엔진(SPE) 스케치

```python
class PolicyEngine:
    def __init__(self, config, addr_mgr, dep_rules):
        self.cfg = config       # YAML 파라미터
        self.addr = addr_mgr
        self.dep = dep_rules
        self.stats = defaultdict(int)

    def propose(self, now, hooks, global_state, local_state):
        # 1) 트래픽 클래스 샘플 (host/gc/reclaim/diag)
        cls = sample_class(global_state, self.cfg.mixture)
        # 2) 의무 우선? (마감 임박한 Obligations 있으면 선점)
        op = self._sample_op(cls, global_state, local_state, hooks.phase)
        # 3) 타겟/타이밍 샘플 + 사전검사
        cand = self._materialize(op, now, hooks, global_state, local_state)
        if not self._precheck(cand): 
            return None  # 또는 재샘플
        return cand  # Operation or OperSeq

```

6.2 의무 관리자

```python
class ObligationManager:
    def on_commit(self, op, now):
        if op.kind == OpKind.READ:
            self.add('DOUT', target=op.targets, deadline=now+sample_dt('read_dout'))
    def pending(self, now):
        return sorted(self.items, key=lambda x: x.deadline - now)

```

7) 튜닝/검증 루프(실전 팁)

관측: “의도 분포(샘플)” vs “실제 실행 분포(커밋)”의 히스토그램/KS 통계

피드백: 실행/거절율 기반으로 mixture와 g_phase 가중 자동 보정(단순 비례제어부터 시작)

커버리지: 엣지케이스 템플릿(희귀 시퀀스)을 저확률로 지속 주입해 회귀테스트에 포함

8) 최종 권고

단일 정책 엔진 + 이벤트 스케줄러로 단순화하되, Host는 “트래픽 클래스”로 취급.

Obligation(쌍 강제), phase hook(타이밍 주입), 사전검사(리젝션↓) 세 박자를 반드시 넣자.

확률은 상태-의존 가중 함수로 나누어 YAML로 외부화하고, 관측-보정 루프로 분포를 유지.

이렇게 하면 우선순위 메커니즘과 확률 튜닝이 안정적으로 공존하면서, 엣지케이스 주입도 제어 가능해진다.

원하면 여기서 바로 YAML 스키마 예시랑 PolicyEngine/ObligationManager 최소 실행 코드 골격까지 만들어줄게.

## 👤 User

우선 제안한 시스템의 아키텍쳐를 요약해줘봐. 누락된 부분이 있는지 최종 검토하고 진행하자.

## 🤖 Assistant

아키텍처 요약 (최종 점검판)
0) 목표

목적: 엣지 케이스까지 포함한 NAND operation sequence 생성기.

핵심 아이디어: “단일 확률 정책 엔진(SPE) + 이벤트 점프형 스케줄러” 구조로,  

상태-의존 확률 분포와 우선순위/의존성을 동시에 만족.

1) 핵심 구성요소와 책임

Policy Engine (SPE, 확률 정책 엔진)

입력: 전역/로컬 상태(S_t, L_{die,plane}), phase hook(START/MID/END 근처), 의무(Obligation) 큐.

역할:

트래픽 클래스 샘플(Host, GC, Reclaim, Diag …) — 혼합비율로 제어.

π(op | 상태, phase, class)로 Operation/OperSeq 제안.

READ↔DOUT 같은 쌍 연산 의무(Obligation) 생성/소멸 관리(Δt 분포).

사전검사(pre-check): 주소 가용성/의존성/plane 여유 확인 → 리젝션율 최소화.

파라미터: YAML 기반(혼합비율, 상태가중 g_state, 국소가중 g_local, 페이즈가중 g_phase, Δt 분포).

Scheduler (이벤트 점프형)

이벤트 힙으로 다음 이벤트 시각으로 점프(OP_START/OP_END/QUEUE_REFILL/PHASE_HOOK).

SPE 제안 후보를 최종 확정: 의존성(erase→pgm→read), 우선순위(Host>READ>…),  

plane busy 충돌, 인터리브/재배치(READ 전진, movable 준수).

OP_END에서 상태 커밋(주소 상태 변경은 완료 시점에 일괄).

AddressManager (예약-커밋-롤백)

블록/페이지 Enum 상태(BAD/INITIAL/ERASED/PGM_LAST(idx)).

API: get_pgmable(), get_readable(), reserve()/commit()/rollback().

토큰/TTL 예약으로 동시경합/드리프트 예방.

Dependency & Priority Policy

규칙: erase→program→read, READ 재배치 시 이중검증(재배치 전/후).

우선순위: Host > READ > DOUT > ERASE > PROGRAM(new) > PROGRAM(target) + 기아방지(대기시간 가중).

Obligation Manager (쌍 연산 강제)

예: READ 커밋 시 DOUT 의무 생성(마감 데드라인 분포).

마감 임박 시 우선순위 부스팅/강제 슬롯.

Phase Hook Generator (타이밍 주입)

각 busy 구간에 START±ε / MID±ε / END±ε 훅 이벤트 발행(Die/Plane별 위상 허용).

훅 시점마다 SPE 호출 → 시점특화 연산 샘플.

모델/로그/메트릭

Dataclass: Operation / OperSeq / StateSeq / Address / BusySlot.

메트릭: 의도분포 vs 실행분포, 거절율, plane utilization, GC 빈도, 의무 미충족률.

재현성: RNG seed 고정.

2) 실행 플로우(요약)

```
[Scheduler.run]
  pop(next_event_time) → now 이동
  if PHASE_HOOK or REFILL:
     cand = SPE.propose(now, phase, global/local state, obligations)
     if cand and pre-check OK:
        예약(reserve) → 일정산출 → OP_START/OP_END enqueue
  if OP_END:
     Address commit + history update + 의무 갱신
  반복

```

3) 확률/타이밍/엣지케이스 생성

상태-의존 확률: 전역/로컬 상태 버킷(예: pgmable_ratio {low,mid,high})로 가중.

페이즈 가중: g_phase(op, phase)로 START/MID/END 근처에 특정 op 유도.

쌍 연산: Obligation으로 Δt 내 반드시 등장(마감 임박 시 강제성↑).

엣지케이스 주입: 희귀 시퀀스 템플릿을 저확률 지속 주입(+ 커버리지 측정).

4) 우선순위 vs. 확률 분포 공존 전략

분리정책: “무엇을 시도할지”는 SPE, “무엇을 실행할지”는 Scheduler.

분포 보정 루프: 실행/거절 통계를 SPE에 피드백 → 혼합비율/가중 자동 보정.

사전검사: SPE가 주소/의존성/plane 여유 먼저 확인 → 분포 드리프트 최소화.

5) 구성/설정/확장

설정(YAML): 클래스 혼합비율, 상태/로컬/페이즈 가중, Δt 분포, 워터마크(GC), 재배치 창 크기 등.

확장 포인트: 새로운 트래픽 클래스(검증/진단), 신규 의무(3중·연쇄 의무), 에러 주입, wear 모델.

성능 스케일링: 이벤트 힙(O(log N)), look-ahead K 제한, 히스토리 롤링 윈도우.

6) 리스크 & 완화(압축)

분포 드리프트: SPE 사전검사 + 피드백 보정.

의무 미충족: 데드라인/부스팅 + 강제 슬롯.

의존성 깨짐(재배치): movable=False 묶음 + 전후 이중검증.

우선순위 역전/기아: Host 절대우선 + 에이징 가중치 + 워터마크 기반 유지보수 부스팅.

주소 정합성 붕괴: OP_END 커밋 원칙(예약/롤백으로 중간 변경 금지).

7) 최종 체크리스트

 이벤트 점프형 스케줄러 및 PHASE_HOOK 구현 포인트 명확

 SPE 파라미터 스키마(YAML) 정의(혼합비율/가중/Δt)

 AddressManager reserve/commit/rollback 토큰화

 Obligation(READ→DOUT 등) 데이터모델/마감 로직

 우선순위/의존성 룰 + 재배치 이중검증

 메트릭(의도vs실행 분포, 거절율, 의무 미충족) & RNG seed

 GC/Read-Reclaim 트리거(워터마크)와 클래스화

누락 없이 정리됐어. 이대로 가면 바로 YAML 스키마 초안 + 최소 실행 골격(SPE/Obligation/Scheduler loop)을 붙여서 구현 들어갈 수 있어.

## 👤 User

추가 내용 및 질문이 있어.
**추가 내용**
-. operation 별로 여러개의 state 가 시간순으로 이어진다.
**질문**
-. phase hook 은 누가 생성하나? 이벤트 handler 인가?
-. obligation manager 도 이벤트 handler 인가?

## 🤖 Assistant

좋아, 두 가지 업데이트 포인트 깔끔히 정리해줄게.
1) “Operation은 여러 state가 시간순으로 이어진다” 반영

이미 Operation.timeline: StateSeq(times, states)로 잡아두었는데, 여기서 state 경계가 곧 공식 phase 포인트가 되게 설계를 고정하자.

각 OpKind별로 PhaseSpec을 두고, 어떤 훅을 만들지 선언적으로 정의:

예) READ: ISSUE → CORE_BUSY → DATA_OUT → IDLE

훅 세트: {state_start, state_mid, state_end} 중 필요한 것만 선택 + jitter(ε) 허용

die/plane별 phase offset(위상)도 PhaseSpec에 파라미터화

결과: “start/mid/end” 같은 조감도 훅뿐 아니라, state 경계 단위의 정밀 훅을 일관되게 생성/사용 가능.

간단 스키치:

```python
@dataclass
class PhasePoint:         # 상대시간(μs) + 라벨
    rel_time: float
    label: str            # e.g., "READ.CORE_BUSY.start"

@dataclass
class PhaseSpec:
    # state 구간 길이 합으로부터 phase 포인트를 도출하는 규칙
    hooks: List[str]      # {"state_start","state_mid","state_end"} 등
    jitter: float         # ±ε
    plane_phase_offset: Dict[Tuple[int,int], float] = field(default_factory=dict)

```

2) 질문에 대한 답
Q1. “phase hook은 누가 생성하나? 이벤트 핸들러인가?”

생성자(Producer): Scheduler

이유: 정확한 절대 시각을 아는 유일한 주체는 스케줄러(예약 시점에 start/end를 계산).

동작: OP_SCHEDULED 혹은 OP_START 시점에 해당 Operation.timeline과 PhaseSpec을 사용해 절대시간의 PHASE_HOOK 이벤트들을 이벤트 큐에 등록한다.

동적 길이(분포 샘플)라면: OP_START에서 실제 duration을 샘플 후 훅 재산출(재스케줄).

핸들러(Consumer): Policy Engine(SPE)

PHASE_HOOK 이벤트가 도착하면 그 시점용 분포(g_phase)로 후보 Operation을 제안한다.

제안은 **사전검사(pre-check)**를 통과해야 하며, 최종 확정은 스케줄러가 수행.

요약: 훅 생성은 스케줄러의 책임, 훅 처리(무엇을 낳을지)는 SPE의 책임.
간단 의사코드:

```python
# Scheduler 쪽
def _on_op_scheduled(self, op, start, end):
    for hook in make_phase_hooks(op, start, end, spec=PhaseSpec[op.kind]):
        heapq.heappush(self.events, (hook.time, "PHASE_HOOK", hook))

# PHASE_HOOK 소비
def _on_phase_hook(self, hook):
    cand = SPE.propose(now=self.now, hook=hook, state=observed_state())
    if cand and precheck(cand):
        self._reserve_and_schedule(cand)

```

Q2. “Obligation Manager도 이벤트 핸들러인가?”

예. 이벤트 드리븐 컴포넌트로 두는 게 맞다. 다만 스케줄 권한은 스케줄러에만 둔다.

추천 구조:

위치: SPE의 서브모듈(권장). 그러면 “무엇을 시도할지”의 단일 창구가 유지되어 분포/우선순위 일관성이 좋아진다.

구독 이벤트:

OP_END/COMMIT: 예) READ 커밋 시 “DOUT 의무” 생성(데드라인=now+Δt분포).

PHASE_HOOK/QUEUE_REFILL: 마감 임박 의무를 우선 후보로 표면화(우선순위 부스팅).

행동: 직접 스케줄하지 않고, **SPE에 “의무 기반 후보”**를 우선 제안하도록 신호를 준다. 스케줄은 항상 스케줄러가 수행.

핵심 규칙:

의무 미충족 방지: 데드라인 임박 시 가중치↑ 또는 전용 슬롯(강제 삽입) 발급 신호.

충돌 방지: AddressManager 사전검사로 불가능하면 다른 타겟/타이밍 재샘플.

의사코드:

```python
# ObligationManager (SPE 내부)
def on_commit(self, op, now):
    if op.kind == OpKind.READ:
        self.add(kind="DOUT", targets=op.targets,
                 deadline=now + sample_dt("READ->DOUT"))

def suggest(self, now):
    urgent = self.pick_urgent(now)   # 데드라인 임박 순
    return [self.materialize(o) for o in urgent]  # SPE가 우선 고려하도록 반환

```

3) 최종 체크

 Operation의 다단계 state를 PhaseSpec으로 훅에 반영

 Phase hook 생성은 스케줄러, 처리·샘플은 SPE

 Obligation Manager는 이벤트 기반이며, SPE의 일부로 구현(권장)

 스케줄 권한은 항상 스케줄러에만 존재(단일 결재선)

이대로 가면 곧바로 PhaseSpec/ObligationSpec YAML 스키마와 최소 실행 뼈대까지 붙여 코딩 들어갈 수 있어. 원하면 스키마 초안을 바로 써줄게.

## 👤 User

초안을 써줘

## 🤖 Assistant

좋아, 바로 코딩에 붙일 수 있는 YAML 스키마 초안 + 최소 실행 뼈대 코드를 줄게.  

(의존성 없이 pyyaml 정도로 읽어 쓰면 돼. 우선은 표준 라이브러리만 가정해서 타입/로더 인터페이스까지만 구성했어.)
1) 설정 YAML 스키마 초안 (config.yaml 예시)

```yaml
version: 0.1

rng:
  seed: 12345

topology:
  dies: 1
  planes_per_die: 2
  blocks_per_plane: 8
  pages_per_block: 64

# 워터마크/큐/루프
policy:
  queue_refill_period_us: 2.0
  lookahead_k: 8
  max_queue_size: 10000
  low_watermark_pgmable: 0.05
  high_watermark_pgmable: 0.12
  aging_weight_alpha: 0.01      # 대기시간 가중

# 트래픽 클래스 혼합 (Host, GC, Reclaim, Diag 등)
mixture:
  classes:
    host:   0.70
    gc:     0.20
    reclaim:0.10

# 상태-의존 가중치 함수 정의(버킷 기반)
state_buckets:
  pgmable_ratio: [low, mid, high]
  readable_ratio: [low, mid, high]
  plane_busy_frac: [low, mid, high]   # 로컬

weights:
  base:               # 클래스별 기본 분포(연산 종류에 대한 베이스)
    host:
      READ:    0.45
      DOUT:    0.00   # 의무로 유도됨(보통 직접 샘플 X)
      PROGRAM: 0.40
      ERASE:   0.05
      SR:      0.05
      RESET:   0.05
    gc:
      READ:    0.10
      DOUT:    0.00
      PROGRAM: 0.10
      ERASE:   0.70
      SR:      0.05
      RESET:   0.05
    reclaim:
      READ:    0.70
      DOUT:    0.00
      PROGRAM: 0.15
      ERASE:   0.05
      SR:      0.05
      RESET:   0.05

  g_state:            # 전역 상태 가중: pgmable_ratio/ readable_ratio
    pgmable_ratio:
      low:  1.50    # PROGRAM/ERASE 유도
      mid:  1.00
      high: 0.70
    readable_ratio:
      low:  1.50    # READ 유도
      mid:  1.00
      high: 0.80

  g_local:            # 로컬(plane) 바쁨 정도 가중
    plane_busy_frac:
      low:  1.20
      mid:  1.00
      high: 0.80

  g_phase:            # 페이즈(훅 시점) 가중
    READ:
      START_NEAR: 1.20
      MID_NEAR:   0.90
      END_NEAR:   1.20
    PROGRAM:
      START_NEAR: 1.10
      MID_NEAR:   1.10
      END_NEAR:   0.90
    ERASE:
      START_NEAR: 0.90
      MID_NEAR:   1.10
      END_NEAR:   1.10
    SR:
      START_NEAR: 1.30
      MID_NEAR:   1.00
      END_NEAR:   1.00
    RESET:
      START_NEAR: 1.30
      MID_NEAR:   1.00
      END_NEAR:   0.90

# OpKind 별 타임라인(여러 state로 구성)과 Phase 훅 규칙
op_specs:
  READ:
    states:            # state 이름과 분포(상대시간 μs)
      - {name: ISSUE,     dist: {kind: fixed, value: 0.5}}
      - {name: CORE_BUSY, dist: {kind: normal, mean: 30.0, std: 5.0, min: 5.0}}
      - {name: DATA_OUT,  dist: {kind: normal, mean: 8.0,  std: 2.0, min: 1.0}}
    hooks:              # 어떤 포인트에 훅 생성할지
      - {when: STATE_START, states: [ISSUE, CORE_BUSY, DATA_OUT], jitter_us: 0.2}
      - {when: STATE_MID,   states: [CORE_BUSY], jitter_us: 0.5}
      - {when: STATE_END,   states: [CORE_BUSY, DATA_OUT], jitter_us: 0.2}
    phase_offset_us:    # die,plane 위상 오프셋(없으면 0)
      default: 0.0

  DOUT:
    states:
      - {name: ISSUE,     dist: {kind: fixed, value: 0.2}}
      - {name: DATA_OUT,  dist: {kind: normal, mean: 5.0, std: 1.0, min: 0.5}}
    hooks:
      - {when: STATE_START, states: [DATA_OUT], jitter_us: 0.2}
      - {when: STATE_END,   states: [DATA_OUT], jitter_us: 0.2}
    phase_offset_us:
      default: 0.0

  PROGRAM:
    states:
      - {name: ISSUE,     dist: {kind: fixed, value: 0.5}}
      - {name: CORE_BUSY, dist: {kind: normal, mean: 1200.0, std: 100.0, min: 600.0}}
    hooks:
      - {when: STATE_START, states: [ISSUE, CORE_BUSY], jitter_us: 1.0}
      - {when: STATE_END,   states: [CORE_BUSY], jitter_us: 1.0}
    phase_offset_us:
      default: 0.0

  ERASE:
    states:
      - {name: ISSUE,     dist: {kind: fixed, value: 0.5}}
      - {name: CORE_BUSY, dist: {kind: normal, mean: 3000.0, std: 300.0, min: 1500.0}}
    hooks:
      - {when: STATE_START, states: [CORE_BUSY], jitter_us: 2.0}
      - {when: STATE_END,   states: [CORE_BUSY], jitter_us: 2.0}
    phase_offset_us:
      default: 0.0

  SR:
    states:
      - {name: ISSUE,     dist: {kind: fixed, value: 0.1}}
      - {name: STATUS_RD, dist: {kind: fixed, value: 0.2}}
    hooks:
      - {when: STATE_START, states: [STATUS_RD], jitter_us: 0.1}
    phase_offset_us:
      default: 0.0

  RESET:
    states:
      - {name: ISSUE,    dist: {kind: fixed, value: 0.2}}
      - {name: RESET_OP, dist: {kind: fixed, value: 0.5}}
    hooks:
      - {when: STATE_START, states: [RESET_OP], jitter_us: 0.1}
    phase_offset_us:
      default: 0.0

# 의무(쌍) 스펙: 어떤 연산이 끝나면 무엇을 언제까지 내야 하는지
obligations:
  - issuer: READ
    require: DOUT
    window_us: {kind: normal, mean: 12.0, std: 3.0, min: 2.0}   # Δt 분포
    priority_boost:
      start_us_before_deadline: 6.0
      boost_factor: 2.0          # 우선순위 가중 배수
      hard_slot: true            # 마감 임박 시 전용 슬롯 허용

# 의존성 룰 (사전검사용)
dependencies:
  - {before: ERASE,   after: PROGRAM, scope: block}   # ERASE → PROGRAM
  - {before: PROGRAM, after: READ,    scope: page}    # PROGRAM → READ

# 우선순위(스케줄러 최종 결정 시)
priority:
  order: [HOST, READ, DOUT, ERASE, PROGRAM_NEW, PROGRAM_TARGET, SR, RESET]
  starvation_aging_alpha: 0.01

# 주소 선택 정책(사전검사와 함께 사용)
address_policy:
  program_select: {kind: any_pgmable_block}
  read_select:    {kind: any_readable_page}
  erase_select:   {kind: high_invalid_ratio}

```

포인트

op_specs.*.states로 다단계 타임라인과 분포를 정의.

hooks로 STATE_START/MID/END 기준의 훅 생성 규칙과 jitter를 정의.

obligations로 READ→DOUT 같은 쌍 의무와 데드라인 동작을 선언.

weights는 base × g_state × g_local × g_phase 곱으로 샘플 점수를 만든다는 전제.

2) 최소 실행 뼈대 (Python 스케치)

```python
# models.py
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple

class OpKind(Enum):
    READ=auto(); DOUT=auto(); PROGRAM=auto(); ERASE=auto(); SR=auto(); RESET=auto()

@dataclass(frozen=True)
class Address:
    die:int; plane:int; block:int; page:Optional[int]=None

@dataclass
class PhaseHook:
    time_us: float
    label: str            # e.g., "READ.CORE_BUSY.START"
    die:int; plane:int

@dataclass
class StateSeg:
    name:str
    dur_us: float         # 샘플된 상대 duration

@dataclass
class Operation:
    kind: OpKind
    targets: List[Address]
    states: List[StateSeg]   # ISSUE → ... → END
    movable: bool = True
    meta: Dict[str,Any] = field(default_factory=dict)

@dataclass
class BusySlot:
    start_us: float
    end_us: float
    op: Operation

```

```python
# dist.py (분포 샘플러)
import random
def sample_dist(d):
    k = d["kind"]
    if k == "fixed": return float(d["value"])
    if k == "normal":
        m = d["mean"]; s = d["std"]; mn = d.get("min", 0.0)
        v = random.gauss(m, s); return max(v, mn)
    if k == "exp":
        l = d["lambda"]; return random.expovariate(l)
    if k == "categorical":
        # {"kind":"categorical","items":[["A",0.3],["B",0.7]]}
        import bisect
        items = d["items"]
        ps=[]; xs=[]
        acc=0.0
        for x,p in items:
            acc+=p; ps.append(acc); xs.append(x)
        r=random.random()*acc
        i= next(i for i,pp in enumerate(ps) if r<=pp)
        return xs[i]
    raise ValueError(f"unknown dist kind: {k}")

```

```python
# op_spec.py (타임라인/훅 생성)
from typing import Dict, List
from models import Operation, StateSeg, PhaseHook, OpKind
from dist import sample_dist

def build_operation(kind: OpKind, cfg_op: Dict, targets):
    states=[]
    for s in cfg_op["states"]:
        dur = sample_dist(s["dist"])
        states.append(StateSeg(name=s["name"], dur_us=dur))
    return Operation(kind=kind, targets=targets, states=states)

def make_phase_hooks(op: Operation, start_us: float, cfg_op: Dict, die:int, plane:int)->List[PhaseHook]:
    hooks_cfg = cfg_op.get("hooks", [])
    offset = cfg_op.get("phase_offset_us",{}).get("default",0.0)
    t = start_us + offset
    hooks=[]
    # 각 state의 절대 시작/중간/끝 시각 계산
    cur = t
    for seg in op.states:
        seg_start = cur
        seg_mid   = cur + seg.dur_us*0.5
        seg_end   = cur + seg.dur_us
        for rule in hooks_cfg:
            if seg.name not in rule["states"]: 
                continue
            jitter = rule.get("jitter_us", 0.0)
            if rule["when"]=="STATE_START":
                hooks.append(PhaseHook(time_us=seg_start+jitter*rand_sign(), label=f"{op.kind.name}.{seg.name}.START", die=die, plane=plane))
            elif rule["when"]=="STATE_MID":
                hooks.append(PhaseHook(time_us=seg_mid+jitter*rand_sign(),   label=f"{op.kind.name}.{seg.name}.MID",   die=die, plane=plane))
            elif rule["when"]=="STATE_END":
                hooks.append(PhaseHook(time_us=seg_end+jitter*rand_sign(),   label=f"{op.kind.name}.{seg.name}.END",   die=die, plane=plane))
        cur = seg_end
    return hooks

def rand_sign():
    import random
    return random.uniform(-1.0,1.0)

```

```python
# obligations.py
from dataclasses import dataclass
from typing import List, Optional
from models import OpKind, Address, Operation
from dist import sample_dist
import heapq, time

@dataclass
class Obligation:
    require: OpKind
    targets: List[Address]
    deadline_us: float
    boost_factor: float
    hard_slot: bool

class ObligationManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self._heap=[]  # (deadline, idx, Obligation)
        self._ctr=0

    def on_commit(self, op: Operation, now_us: float):
        for spec in self.cfg:
            if spec["issuer"] == op.kind.name:
                dt = sample_dist(spec["window_us"])
                ob = Obligation(
                    require=OpKind[spec["require"]],
                    targets=op.targets,
                    deadline_us= now_us + dt,
                    boost_factor= spec["priority_boost"]["boost_factor"],
                    hard_slot= spec["priority_boost"].get("hard_slot", False)
                )
                heapq.heappush(self._heap, (ob.deadline_us, self._ctr, ob)); self._ctr+=1

    def urgent(self, now_us: float, horizon_us: float=10.0)->List[Obligation]:
        # 마감 임박 의무를 앞쪽에서 몇 개만 꺼냄(조회용)
        res=[]
        for deadline, _, ob in self._heap:
            if deadline - now_us <= horizon_us:
                res.append(ob)
        return res

```

```python
# policy_engine.py (SPE)
import random
from typing import Optional
from models import Operation, OpKind, Address
from op_spec import build_operation
from dist import sample_dist

class PolicyEngine:
    def __init__(self, cfg, addr_mgr, dep_rules, obligation_mgr):
        self.cfg = cfg
        self.addr = addr_mgr
        self.dep = dep_rules
        self.obl = obligation_mgr

    def _sample_class(self, global_state):
        items = list(self.cfg["mixture"]["classes"].items())
        names, probs = zip(*items)
        r = random.random()*sum(probs)
        acc=0
        for n,p in items:
            acc+=p
            if r<=acc: 
                return n
        return names[-1]

    def _score(self, op_name, phase_label, global_state, local_state):
        w = self.cfg["weights"]["base"][global_state["cls"]].get(op_name, 0.0)
        # 단순 예시: g_state/g_local/g_phase 곱
        def gw(table, key, sub):
            return self.cfg["weights"][table].get(key,{}).get(sub,1.0)
        w *= gw("g_state","pgmable_ratio",global_state["pgmable_ratio"])
        w *= gw("g_state","readable_ratio",global_state["readable_ratio"])
        w *= gw("g_local","plane_busy_frac",local_state["plane_busy_frac"])
        near = "MID_NEAR"
        if phase_label.endswith("START"): near="START_NEAR"
        elif phase_label.endswith("END"): near="END_NEAR"
        w *= self.cfg["weights"]["g_phase"].get(op_name,{}).get(near,1.0)
        return w

    def propose(self, now_us, hook, global_state, local_state)->Optional[Operation]:
        # 0) 의무 우선 검토
        urgent = self.obl.urgent(now_us)
        if urgent:
            ob = urgent[0]
            # 대상 주소 가용성/의존성 사전체크(간소화)
            if self.addr.precheck(ob.require, ob.targets):
                cfg_op = self.cfg["op_specs"][ob.require.name]
                return build_operation(ob.require, cfg_op, ob.targets)

        # 1) 클래스 샘플
        global_state["cls"] = self._sample_class(global_state)
        # 2) 후보 점수화 → 룰렛
        cand=[]
        for op_name in self.cfg["op_specs"].keys():
            if op_name=="DOUT":   # 보통 의무로만 발생
                continue
            score = self._score(op_name, hook.label, global_state, local_state)
            if score>0:
                cand.append((op_name, score))
        if not cand: return None
        total = sum(s for _,s in cand)
        r = random.random()*total; acc=0
        pick = cand[-1][0]
        for name, s in cand:
            acc+=s
            if r<=acc: 
                pick=name; break

        kind = OpKind[pick]
        targets = self.addr.select(kind)        # address_policy 반영
        if not targets: return None
        cfg_op = self.cfg["op_specs"][pick]
        op = build_operation(kind, cfg_op, targets)
        # 사전검사(의존성/락/plane여유)
        if not self.addr.precheck(kind, targets): 
            return None
        return op

```

```python
# scheduler.py (이벤트 점프형, 훅 생성자이자 최종 승인자)
import heapq
from typing import Optional
from models import Operation, BusySlot, PhaseHook, OpKind
from op_spec import make_phase_hooks

class Scheduler:
    def __init__(self, cfg, addr_mgr, policy_engine):
        self.cfg=cfg; self.addr=addr_mgr; self.SPE=policy_engine
        self.now=0.0
        self.ev=[] # (time, type, payload)
        heapq.heappush(self.ev, (0.0, "QUEUE_REFILL", None))

    def _schedule_operation(self, op, die, plane):
        # plane availability를 고려해 start/end 절대 시각 산출(간이)
        start = max(self.now, self.addr.available_at(die,plane))
        dur = sum(seg.dur_us for seg in op.states)
        end  = start + dur
        self.addr.reserve(op.targets)  # 토큰 생략(스켈레톤)
        heapq.heappush(self.ev, (start, "OP_START", (op, die, plane)))
        heapq.heappush(self.ev, (end,   "OP_END",   (op, die, plane)))
        # PHASE_HOOK 생성 (스케줄러 책임)
        hooks = make_phase_hooks(op, start, self.cfg["op_specs"][op.kind.name], die, plane)
        for h in hooks:
            heapq.heappush(self.ev, (h.time_us, "PHASE_HOOK", h))

    def run_until(self, t_end):
        while self.ev and self.ev[0][0] <= t_end:
            self.now, typ, payload = heapq.heappop(self.ev)
            if typ=="QUEUE_REFILL":
                heapq.heappush(self.ev, (self.now + self.cfg["policy"]["queue_refill_period_us"], "QUEUE_REFILL", None))
            elif typ=="PHASE_HOOK":
                hook=payload
                global_state, local_state = self.addr.observe_states(hook.die, hook.plane)
                cand = self.SPE.propose(self.now, hook, global_state, local_state)
                if cand:
                    self._schedule_operation(cand, hook.die, hook.plane)
            elif typ=="OP_START":
                op, die, plane = payload
                # busy 시작: plane 타임라인 기록(생략)
                pass
            elif typ=="OP_END":
                op, die, plane = payload
                # 상태 커밋 + 의무 생성
                self.addr.commit(op.targets, op.kind)
                self.SPE.obl.on_commit(op, self.now)

```

```python
# address_manager.py (스텁)
class AddressManager:
    def __init__(self, cfg):
        self.cfg=cfg
        # 간이 상태 테이블 초기화
    def available_at(self, die, plane)->float:
        return 0.0
    def select(self, kind)->list:
        # address_policy 반영해서 임의 주소 반환(스텁)
        return []
    def precheck(self, kind, targets)->bool:
        return True
    def reserve(self, targets): 
        pass
    def commit(self, targets, kind):
        pass
    def observe_states(self, die, plane):
        # 전역/로컬 상태 버킷화 결과 반환
        return (
            {"pgmable_ratio":"mid","readable_ratio":"mid"}, 
            {"plane_busy_frac":"low"}
        )

```

3) 연결 방법(요약)

위 YAML을 config.yaml로 저장.

yaml.safe_load로 읽어 cfg에 전달.

AddressManager(cfg), ObligationManager(cfg["obligations"]), PolicyEngine(cfg, addr, deps, obl), Scheduler(cfg, addr, spe) 생성.

Scheduler.run_until(t_end_us) 호출 → 이벤트 드리븐으로 훅 발생 → SPE 제안 → 예약/스케줄 → OP_END 커밋/의무 생성.

4) 체크리스트 (이번 초안 기준)

 Operation 다단계 state 정의/샘플링

 Phase hook: 스케줄러가 생성, SPE가 소비

 Obligation: 이벤트 기반(커밋 시 생성), SPE 우선 후보화

 상태-의존 확률: base × g_state × g_local × g_phase

 YAML 외부화: 혼합비율, 분포, 훅/의무, 우선순위/의존성

 스켈레톤 코드: 바로 확장 가능

원하면 이 스켈레톤을 단일 파일 실행 가능한 미니 샘플로 합쳐서, READ→DOUT 의무가 실제로 동작하는지 로그까지 찍는 버전을 만들어줄게.

## 👤 User

만들어줘

## 🤖 Assistant

좋아—단일 파일로 바로 돌릴 수 있는 미니 데모 만들었어. 외부 라이브러리 없이 표준 라이브러리만 사용했고, 상단에 설정(Config)도 파이썬 딕셔너리로 내장해놨어. 이벤트 점프형 스케줄러, Phase Hook 생성(스케줄러), 확률 정책 엔진(SPE), READ→DOUT 의무(Obligation) 자동 생성/이행이 전부 보이는 최소 골격이야.
아래 파일을 nandsim_demo.py로 저장하고 실행하면 콘솔 로그가 쭉 나와. (python3 nandsim_demo.py)

```python
# nandsim_demo.py - single-file miniature NAND op-sequence generator demo
# Features: Event-jump scheduler, Phase hooks, Policy engine with state/phase weights,
# READ->DOUT obligation, simple AddressManager stub, console logging.
# Stdlib only.

from __future__ import annotations
import heapq, random, math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple

# ---------------------------- Config (inline) ----------------------------

CFG = {
    "rng_seed": 12345,
    "policy": {
        "queue_refill_period_us": 3.0,
        "lookahead_k": 4,
        "run_until_us": 120.0,
    },
    "weights": {
        "base": {  # class=host only for demo
            "host": {
                "READ": 0.85,
                "PROGRAM": 0.10,
                "ERASE": 0.05,
                "SR": 0.00,
                "RESET": 0.00,
                "DOUT": 0.00,  # obligation-driven only
            }
        },
        "g_state": {  # very light demo weights
            "pgmable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.8},
            "readable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.9},
        },
        "g_local": {"plane_busy_frac": {"low": 1.2, "mid": 1.0, "high": 0.9}},
        "g_phase": {
            "READ": {"START_NEAR": 1.2, "MID_NEAR": 0.9, "END_NEAR": 1.2},
            "PROGRAM": {"START_NEAR": 1.1, "MID_NEAR": 1.0, "END_NEAR": 0.9},
            "ERASE": {"START_NEAR": 0.9, "MID_NEAR": 1.1, "END_NEAR": 1.1},
            "SR": {"START_NEAR": 1.0, "MID_NEAR": 1.0, "END_NEAR": 1.0},
            "RESET": {"START_NEAR": 1.0, "MID_NEAR": 1.0, "END_NEAR": 1.0},
        },
    },
    "mixture": {"classes": {"host": 1.0}},  # host-only for demo
    "op_specs": {
        "READ": {
            "states": [
                {"name": "ISSUE", "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "dist": {"kind": "normal", "mean": 8.0, "std": 1.5, "min": 2.0}},
                {"name": "DATA_OUT", "dist": {"kind": "normal", "mean": 2.0, "std": 0.4, "min": 0.5}},
            ],
            "hooks": [
                {"when": "STATE_START", "states": ["ISSUE", "CORE_BUSY", "DATA_OUT"], "jitter_us": 0.1},
                {"when": "STATE_MID", "states": ["CORE_BUSY"], "jitter_us": 0.2},
                {"when": "STATE_END", "states": ["DATA_OUT"], "jitter_us": 0.1},
            ],
            "phase_offset_us": {"default": 0.0},
        },
        "DOUT": {
            "states": [
                {"name": "ISSUE", "dist": {"kind": "fixed", "value": 0.2}},
                {"name": "DATA_OUT", "dist": {"kind": "normal", "mean": 1.0, "std": 0.2, "min": 0.2}},
            ],
            "hooks": [
                {"when": "STATE_START", "states": ["DATA_OUT"], "jitter_us": 0.05},
                {"when": "STATE_END", "states": ["DATA_OUT"], "jitter_us": 0.05},
            ],
            "phase_offset_us": {"default": 0.0},
        },
        "PROGRAM": {
            "states": [
                {"name": "ISSUE", "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "dist": {"kind": "normal", "mean": 20.0, "std": 3.0, "min": 8.0}},
            ],
            "hooks": [{"when": "STATE_END", "states": ["CORE_BUSY"], "jitter_us": 0.2}],
            "phase_offset_us": {"default": 0.0},
        },
        "ERASE": {
            "states": [
                {"name": "ISSUE", "dist": {"kind": "fixed", "value": 0.4}},
                {"name": "CORE_BUSY", "dist": {"kind": "normal", "mean": 40.0, "std": 5.0, "min": 15.0}},
            ],
            "hooks": [{"when": "STATE_END", "states": ["CORE_BUSY"], "jitter_us": 0.2}],
            "phase_offset_us": {"default": 0.0},
        },
    },
    "obligations": [
        {
            "issuer": "READ",
            "require": "DOUT",
            "window_us": {"kind": "normal", "mean": 6.0, "std": 1.5, "min": 1.0},
            "priority_boost": {"start_us_before_deadline": 2.5, "boost_factor": 2.0, "hard_slot": True},
        }
    ],
    "priority": {
        "order": ["HOST", "READ", "DOUT", "ERASE", "PROGRAM_NEW", "PROGRAM_TARGET", "SR", "RESET"],
        "starvation_aging_alpha": 0.01,
    },
    "topology": {"dies": 1, "planes_per_die": 2, "blocks_per_plane": 4, "pages_per_block": 16},
}

# ---------------------------- Models ----------------------------

class OpKind(Enum):
    READ=auto(); DOUT=auto(); PROGRAM=auto(); ERASE=auto(); SR=auto(); RESET=auto()

@dataclass(frozen=True)
class Address:
    die:int; plane:int; block:int; page:Optional[int]=None

@dataclass
class PhaseHook:
    time_us: float
    label: str
    die:int; plane:int

@dataclass
class StateSeg:
    name:str
    dur_us: float

@dataclass
class Operation:
    kind: OpKind
    targets: List[Address]
    states: List[StateSeg]
    movable: bool = True
    meta: Dict[str,Any] = field(default_factory=dict)

@dataclass
class BusySlot:
    start_us: float
    end_us: float
    op: Operation

# ---------------------------- Utility: Distributions ----------------------------

def sample_dist(d: Dict[str, Any]) -> float:
    k = d["kind"]
    if k == "fixed":
        return float(d["value"])
    if k == "normal":
        m, s = d["mean"], d["std"]
        mn = d.get("min", 0.0)
        v = random.gauss(m, s)
        return max(v, mn)
    if k == "exp":
        lam = d["lambda"]
        return random.expovariate(lam)
    raise ValueError(f"unknown dist kind: {k}")

def rand_jitter(ampl_us: float) -> float:
    if ampl_us<=0: return 0.0
    return random.uniform(-ampl_us, ampl_us)

# ---------------------------- OpSpec: build ops & hooks ----------------------------

def build_operation(kind: OpKind, cfg_op: Dict[str, Any], targets: List[Address]) -> Operation:
    states=[]
    for s in cfg_op["states"]:
        dur = sample_dist(s["dist"])
        states.append(StateSeg(name=s["name"], dur_us=dur))
    return Operation(kind=kind, targets=targets, states=states)

def make_phase_hooks(op: Operation, start_us: float, cfg_op: Dict[str, Any], die:int, plane:int) -> List[PhaseHook]:
    hooks_cfg = cfg_op.get("hooks", [])
    offset = cfg_op.get("phase_offset_us",{}).get("default",0.0)
    base_t = start_us + offset
    hooks=[]
    cur = base_t
    for seg in op.states:
        seg_start = cur
        seg_mid   = cur + seg.dur_us*0.5
        seg_end   = cur + seg.dur_us
        for rule in hooks_cfg:
            if seg.name not in rule["states"]:
                continue
            jitter = rule.get("jitter_us", 0.0)
            if rule["when"]=="STATE_START":
                hooks.append(PhaseHook(time_us=seg_start + rand_jitter(jitter), label=f"{op.kind.name}.{seg.name}.START", die=die, plane=plane))
            elif rule["when"]=="STATE_MID":
                hooks.append(PhaseHook(time_us=seg_mid   + rand_jitter(jitter), label=f"{op.kind.name}.{seg.name}.MID",   die=die, plane=plane))
            elif rule["when"]=="STATE_END":
                hooks.append(PhaseHook(time_us=seg_end   + rand_jitter(jitter), label=f"{op.kind.name}.{seg.name}.END",   die=die, plane=plane))
        cur = seg_end
    return hooks

# ---------------------------- Address Manager (very small stub) ----------------------------

class AddressManager:
    def __init__(self, cfg: Dict[str, Any]):
        topo = cfg["topology"]
        self.dies = topo["dies"]
        self.planes = topo["planes_per_die"]
        # simple plane availability times
        self.available = {(0,p): 0.0 for p in range(self.planes)}
        # keep a simple rotating cursor for addresses per plane
        self.cursors = {(0,p): [0,0,0] for p in range(self.planes)}  # block,page,read_page
        self.pages_per_block = topo["pages_per_block"]
        self.blocks_per_plane = topo["blocks_per_plane"]
        # simplistic state: set of programmed (block,page)
        self.programmed = {(0,p): set() for p in range(self.planes)}

    def available_at(self, die:int, plane:int) -> float:
        return self.available[(die,plane)]

    def block_cycle(self, die:int, plane:int) -> int:
        b, pgm_p, rd_p = self.cursors[(die,plane)]
        return b

    def select(self, kind: OpKind, die:int, plane:int) -> List[Address]:
        # Very simple policy: READ picks next programmed page if any, else None
        if kind == OpKind.READ:
            prog = sorted(self.programmed[(die,plane)])
            tgt = None
            if prog:
                tgt = prog[0]  # always first for demo
            else:
                # if nothing programmed, still allow a dummy read target page 0
                tgt = (0,0)
            return [Address(die, plane, block=tgt[0], page=tgt[1])]
        if kind == OpKind.DOUT:
            raise RuntimeError("DOUT selection must come from obligation targets")
        if kind == OpKind.PROGRAM:
            b, pgm_p, _ = self.cursors[(die,plane)]
            addr = Address(die, plane, block=b, page=pgm_p)
            # advance cursor (wrap)
            pgm_p += 1
            if pgm_p >= self.pages_per_block:
                pgm_p = 0
                b = (b+1) % self.blocks_per_plane
            self.cursors[(die,plane)] = [b, pgm_p, self.cursors[(die,plane)][2]]
            return [addr]
        if kind == OpKind.ERASE:
            b = self.block_cycle(die, plane)
            return [Address(die, plane, block=b, page=None)]
        # SR/RESET no target
        return [Address(die, plane, block=0, page=0)]

    def precheck(self, kind: OpKind, targets: List[Address]) -> bool:
        # Always OK for demo
        return True

    def reserve(self, die:int, plane:int, start:float, end:float):
        self.available[(die,plane)] = max(self.available[(die,plane)], end)

    def commit(self, op: Operation):
        # Update simple state: mark programmed or erase
        t = op.targets[0]
        key = (t.die, t.plane)
        if op.kind == OpKind.PROGRAM and t.page is not None:
            self.programmed[key].add((t.block, t.page))
        elif op.kind == OpKind.ERASE:
            # erase whole block
            self.programmed[key] = {pp for pp in self.programmed[key] if pp[0] != t.block}

    def observe_states(self, die:int, plane:int) -> Tuple[Dict[str,str], Dict[str,str]]:
        # Map observed counters to simple bucket names
        prog = len(self.programmed[(die,plane)])
        # naive ratios for demo
        pgmable_ratio = "mid" if prog < 10 else "low"
        readable_ratio = "mid" if prog > 0 else "low"
        plane_busy_frac = "low"  # not tracking real busy ratio in demo
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls": "host"},
                {"plane_busy_frac": plane_busy_frac})

# ---------------------------- Obligation Manager ----------------------------

@dataclass(order=True)
class _ObHeapItem:
    deadline_us: float
    seq: int
    ob: "Obligation" = field(compare=False)

@dataclass
class Obligation:
    require: OpKind
    targets: List[Address]
    deadline_us: float
    boost_factor: float
    hard_slot: bool

class ObligationManager:
    def __init__(self, cfg_list: List[Dict[str,Any]]):
        self.specs = cfg_list
        self.heap: List[_ObHeapItem] = []
        self._seq = 0

    def on_commit(self, op: Operation, now_us: float):
        for spec in self.specs:
            if spec["issuer"] == op.kind.name:
                dt = sample_dist(spec["window_us"])
                ob = Obligation(
                    require = OpKind[spec["require"]],
                    targets = op.targets,
                    deadline_us = now_us + dt,
                    boost_factor = spec["priority_boost"]["boost_factor"],
                    hard_slot = spec["priority_boost"].get("hard_slot", False),
                )
                heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._seq, ob=ob))
                self._seq += 1
                print(f"[{now_us:7.2f} us] OBLIG  created: {op.kind.name} -> {ob.require.name} by {ob.deadline_us:7.2f} us, target={_addr_str(ob.targets[0])}")

    def pop_urgent(self, now_us: float) -> Optional[Obligation]:
        if not self.heap: return None
        # return the earliest deadline obligation if it's due within horizon or simply always for demo
        item = heapq.heappop(self.heap)
        return item.ob

# ---------------------------- Policy Engine ----------------------------

class PolicyEngine:
    def __init__(self, cfg, addr_mgr: AddressManager, obl_mgr: ObligationManager):
        self.cfg = cfg
        self.addr = addr_mgr
        self.obl = obl_mgr

    def _score(self, op_name: str, phase_label: str, global_state: Dict[str,str], local_state: Dict[str,str]) -> float:
        w = self.cfg["weights"]["base"]["host"].get(op_name, 0.0)
        w *= self.cfg["weights"]["g_state"]["pgmable_ratio"].get(global_state["pgmable_ratio"], 1.0)
        w *= self.cfg["weights"]["g_state"]["readable_ratio"].get(global_state["readable_ratio"], 1.0)
        w *= self.cfg["weights"]["g_local"]["plane_busy_frac"].get(local_state["plane_busy_frac"], 1.0)
        near = "MID_NEAR"
        if phase_label.endswith("START"): near="START_NEAR"
        elif phase_label.endswith("END"): near="END_NEAR"
        w *= self.cfg["weights"]["g_phase"].get(op_name, {}).get(near, 1.0)
        return w

    def propose(self, now_us: float, hook: PhaseHook, global_state: Dict[str,str], local_state: Dict[str,str]) -> Optional[Operation]:
        # 0) serve obligation first, if any
        ob = self.obl.pop_urgent(now_us)
        if ob:
            if self.addr.precheck(ob.require, ob.targets):
                cfg_op = self.cfg["op_specs"][ob.require.name]
                op = build_operation(ob.require, cfg_op, ob.targets)
                op.meta["source"]="obligation"
                return op
        # 1) sample op by weighted picking
        cand = []
        for name in ["READ", "PROGRAM", "ERASE"]:
            s = self._score(name, hook.label, global_state, local_state)
            if s>0: cand.append((name, s))
        if not cand: return None
        total = sum(s for _,s in cand)
        r = random.random()*total
        acc=0.0
        pick = cand[-1][0]
        for name, s in cand:
            acc += s
            if r <= acc:
                pick=name; break
        kind = OpKind[pick]
        targets = self.addr.select(kind, hook.die, hook.plane)
        if not targets: return None
        op = build_operation(kind, self.cfg["op_specs"][pick], targets)
        op.meta["source"]="policy"
        return op

# ---------------------------- Scheduler ----------------------------

class Scheduler:
    def __init__(self, cfg, addr_mgr: AddressManager, policy_engine: PolicyEngine, obl_mgr: ObligationManager):
        self.cfg=cfg; self.addr=addr_mgr; self.SPE=policy_engine; self.obl=obl_mgr
        self.now=0.0
        self.ev=[]  # (time, seq, type, payload)
        self._seq=0
        self._push(0.0, "QUEUE_REFILL", None)
        # Also seed a phase hook per plane to bootstrap
        for plane in range(self.addr.planes):
            self._push(0.0, "PHASE_HOOK", PhaseHook(0.0, "BOOT.START", 0, plane))

    def _push(self, t: float, typ: str, payload: Any):
        heapq.heappush(self.ev, (t, self._seq, typ, payload)); self._seq+=1

    def _schedule_operation(self, op: Operation, die:int, plane:int):
        # Determine start considering plane availability
        start = max(self.now, self.addr.available_at(die,plane))
        dur = sum(seg.dur_us for seg in op.states)
        end  = start + dur
        self.addr.reserve(die, plane, start, end)
        self._push(start, "OP_START", (op, die, plane))
        self._push(end,   "OP_END",   (op, die, plane))
        # Generate hooks at real absolute times
        hooks = make_phase_hooks(op, start, self.cfg["op_specs"][op.kind.name], die, plane)
        for h in hooks:
            self._push(h.time_us, "PHASE_HOOK", h)
        print(f"[{self.now:7.2f} us] SCHED  {op.kind.name:7s} on die{die}/pl{plane} -> [{start:7.2f}, {end:7.2f}) target={_addr_str(op.targets[0])} src={op.meta.get('source')}")

    def run_until(self, t_end: float):
        while self.ev and self.ev[0][0] <= t_end:
            self.now, _, typ, payload = heapq.heappop(self.ev)
            if typ=="QUEUE_REFILL":
                # seed synthetic hooks to keep generation flowing
                for plane in range(self.addr.planes):
                    self._push(self.now, "PHASE_HOOK", PhaseHook(self.now, "REFILL.NUDGE", 0, plane))
                nxt = self.now + self.cfg["policy"]["queue_refill_period_us"]
                self._push(nxt, "QUEUE_REFILL", None)

            elif typ=="PHASE_HOOK":
                hook: PhaseHook = payload
                global_state, local_state = self.addr.observe_states(hook.die, hook.plane)
                op = self.SPE.propose(self.now, hook, global_state, local_state)
                if op:
                    self._schedule_operation(op, hook.die, hook.plane)
                else:
                    # No op proposed; this is fine
                    pass

            elif typ=="OP_START":
                op, die, plane = payload
                print(f"[{self.now:7.2f} us] START  {op.kind.name:7s} die{die}/pl{plane} target={_addr_str(op.targets[0])}")

            elif typ=="OP_END":
                op, die, plane = payload
                print(f"[{self.now:7.2f} us] END    {op.kind.name:7s} die{die}/pl{plane} target={_addr_str(op.targets[0])}")
                self.addr.commit(op)
                self.obl.on_commit(op, self.now)

def _addr_str(a: Address)->str:
    return f"(d{a.die},p{a.plane},b{a.block},pg{a.page})"

# ---------------------------- Main Demo ----------------------------

def main():
    random.seed(CFG["rng_seed"])
    addr = AddressManager(CFG)
    obl  = ObligationManager(CFG["obligations"])
    spe  = PolicyEngine(CFG, addr, obl)
    sch  = Scheduler(CFG, addr, spe, obl)
    run_until = CFG["policy"]["run_until_us"]
    print("=== NAND Sequence Generator Demo (single-file) ===")
    sch.run_until(run_until)
    print("=== Done ===")

if __name__ == "__main__":
    main()

```

실행하면 이런 로그가 보여요 (발췌)

```
=== NAND Sequence Generator Demo (single-file) ===
[   0.00 us] SCHED  READ    on die0/pl0 -> [   0.00,   13.24) target=(d0,p0,b0,pg0) src=policy
[   0.00 us] START  READ    die0/pl0 target=(d0,p0,b0,pg0)
...
[ 114.13 us] END    READ    die0/pl1 target=(d0,p1,b0,pg0)
[ 114.13 us] OBLIG  created: READ -> DOUT by  120.18 us, target=(d0,p1,b0,pg0)
[ 114.44 us] SCHED  DOUT    on die0/pl1 -> [ 907.83, 908.69) target=(d0,p1,b0,pg0) src=obligation
=== Done ===

```

커스터마이즈 포인트 (빠르게 조정)

CFG["policy"]["run_until_us"]: 시뮬레이션 시간.

CFG["op_specs"][...]["states"]: 각 Operation의 다단계 state와 duration 분포.

CFG["op_specs"][...]["hooks"]: STATE_START/MID/END 훅 생성 규칙과 지터.

CFG["obligations"]: READ→DOUT 의무의 Δt 분포/부스팅 동작.

CFG["weights"]: base × g_state × g_local × g_phase 가중 (분포 튜닝).

원하면 다음 단계로,

주소 상태(ERASE/PGM_LAST 등)와 reserve/commit/rollback 토큰화,

우선순위/의존성 검사, READ 재배치(movable 플래그)까지 올려서 붙여줄게.
