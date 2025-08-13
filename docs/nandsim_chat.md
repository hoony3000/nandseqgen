---
title: ChatGPT Export
exported_at: 2025-08-13_13-51
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

2) 시스템 개요
구성요소와 책임:

핵심 원칙

3) 데이터 모델 (Python 스켈레톤)

4) Scheduler 설계
4.1 이벤트 기반 루프

4.2 BusyTable/History/Timer

4.3 Operation 추가 절차 (정제)

기존 “until_time”은 Operation.timeline의 상대시간 합으로부터 END 절대시각을 계산하여 이벤트로 관리한다.

5) AddressManager 설계
5.1 상태 표현

5.2 예약/확정/롤백

6) QueueManager 설계
6.1 큐 구조

6.2 우선순위 정책(기본)

6.3 의존성 검사

7) HostReq → OperSeq 변환

8) 재배치(리드 전진) & 인터리브

9) GC & Read-Reclaim 정책
9.1 Garbage Collection

9.2 Read-Reclaim

10) 구현 절차(이벤트 라이프사이클)

11) 구성/파라미터(예시 기본값)

12) 관측/로깅

13) 리스크 & 완화책

14) 테스트 전략(필수 시나리오)

15) 파일 구조 제안

16) 의사코드 스니펫
16.1 스케줄러 핵심

17) “Self-thoughts” 항목 반영(정책화)

18) 결정사항 요약

19) 미해결 이슈 & 권고 기본값 (Critical Questions)

20) 다음 단계(바로 코드로 이어가기)

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

이 구조면 “우선순위 메커니즘”과 “확률 튜닝”이 서로 발을 밟지 않고 같이 굴러가며, 구현도 가장 깔끔하게 나눠져.

2) Host↔NAND 우선순위와 확률 튜닝의 공존성 평가
공존 가능? → 예

구현 난이도 & 리스크

3) “Host와 NAND를 별개 주체로 두지 않는다면?” 기준선 선택

본 목적이 “엣지케이스 포함 시퀀스 생성”이므로, 단일 정책 엔진으로 가고 Host는 한 클래스로 취급하기를 권장.

4) 구체 설계 포인트
4.1 상태-의존 확률(예시)

4.2 쌍 연산 의무(READ↔DOUT 등)

4.3 타이밍/페이즈 주입

4.4 실행 전 사전검사(리젝션 절감)

5) 복잡도/리스크 비교 (요약)

6) 바로 넣기 쉬운 인터페이스 초안
6.1 정책 엔진(SPE) 스케치

6.2 의무 관리자

7) 튜닝/검증 루프(실전 팁)

8) 최종 권고

원하면 여기서 바로 YAML 스키마 예시랑 PolicyEngine/ObligationManager 최소 실행 코드 골격까지 만들어줄게.

## 👤 User

우선 제안한 시스템의 아키텍쳐를 요약해줘봐. 누락된 부분이 있는지 최종 검토하고 진행하자.

## 🤖 Assistant

아키텍처 요약 (최종 점검판)
0) 목표

1) 핵심 구성요소와 책임

2) 실행 플로우(요약)

3) 확률/타이밍/엣지케이스 생성

4) 우선순위 vs. 확률 분포 공존 전략

5) 구성/설정/확장

6) 리스크 & 완화(압축)

7) 최종 체크리스트

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

간단 스키치:

2) 질문에 대한 답
Q1. “phase hook은 누가 생성하나? 이벤트 핸들러인가?”

요약: 훅 생성은 스케줄러의 책임, 훅 처리(무엇을 낳을지)는 SPE의 책임.
간단 의사코드:

Q2. “Obligation Manager도 이벤트 핸들러인가?”

의사코드:

3) 최종 체크

이대로 가면 곧바로 PhaseSpec/ObligationSpec YAML 스키마와 최소 실행 뼈대까지 붙여 코딩 들어갈 수 있어. 원하면 스키마 초안을 바로 써줄게.

## 👤 User

초안을 써줘

## 🤖 Assistant

좋아, 바로 코딩에 붙일 수 있는 YAML 스키마 초안 + 최소 실행 뼈대 코드를 줄게.  

(의존성 없이 pyyaml 정도로 읽어 쓰면 돼. 우선은 표준 라이브러리만 가정해서 타입/로더 인터페이스까지만 구성했어.)
1) 설정 YAML 스키마 초안 (config.yaml 예시)

포인트

2) 최소 실행 뼈대 (Python 스케치)

3) 연결 방법(요약)

4) 체크리스트 (이번 초안 기준)

원하면 이 스켈레톤을 단일 파일 실행 가능한 미니 샘플로 합쳐서, READ→DOUT 의무가 실제로 동작하는지 로그까지 찍는 버전을 만들어줄게.

## 👤 User

만들어줘

## 🤖 Assistant

좋아—단일 파일로 바로 돌릴 수 있는 미니 데모 만들었어. 외부 라이브러리 없이 표준 라이브러리만 사용했고, 상단에 설정(Config)도 파이썬 딕셔너리로 내장해놨어. 이벤트 점프형 스케줄러, Phase Hook 생성(스케줄러), 확률 정책 엔진(SPE), READ→DOUT 의무(Obligation) 자동 생성/이행이 전부 보이는 최소 골격이야.
아래 파일을 nandsim_demo.py로 저장하고 실행하면 콘솔 로그가 쭉 나와. (python3 nandsim_demo.py)

실행하면 이런 로그가 보여요 (발췌)

커스터마이즈 포인트 (빠르게 조정)

원하면 다음 단계로,

## 👤 User

다음 단계로 가기 전에, 각 함수가 call 되는 시점에 대해 잘 정의하고 진행하자. 다음의 함수 들에 대해서 어느 시점에 call 되는지 정의해서, propose 시 거절될 확률을 최대한 낮춰야 해. 그게 단기적인 목표야. 함수는 아래 참조.
- AddressManager class : available_at, precheck, reserver, commit, observe_states
- PolicyEngine class : propose
- ObligationManager : on_commit, pop_urgent
이 함수들이 call 되는 시점을 workflow 를 통해서 구조화하고, propose 직전까지 어떤 context 가 종합되는지 점검하자.

## 🤖 Assistant

좋아. propose 시 거절 확률을 최소화하는 걸 목표로, 각 함수가 언제 호출되는지와 무엇을 준비해서 넘기는지, 이벤트별 워크플로우로 정리했어. (표준화: reserver → reserve 로 명칭 통일)
이벤트별 호출 시점·컨텍스트
A) PHASE_HOOK 도착 시 (핵심 트리거)
목표: 해당 die/plane에 지금 시점에서 실제로 스케줄 가능한 후보만 제안받아 리젝션을 거의 없애기.

이 흐름이면 precheck가 propose 내부에서 선행되므로, Scheduler 단계에서의 거절은 사실상 없음(= 리젝션 최소화).

B) QUEUE_REFILL 도착 시 (부스트/시딩)
목표: 훅 밀도가 낮을 때 생성 리듬 유지 + busy 해제 직전에 제안 준비.

REFILL 자체에서는 propose를 직접 부르지 않고, PHASE_HOOK을 만들어서 동일한 A) 흐름으로 통일하는 걸 권장.

C) OP_START 도착 시

D) OP_END 도착 시 (상태 커밋 & 의무 생성 트리거)

커밋을 항상 의무 생성보다 먼저 수행(상태 정합성 보장).

함수별 “정확한 호출 시점” 요약 표

주의: reserve는 Scheduler만 호출(단일 결재선), precheck는 SPE 내부에서 미리 호출(리젝션 방지).

propose 직전 “컨텍스트 패킷” (권장 형태)

SPE 내부 동작(권장 순서):

리젝션(거절) 최소화 팁

필요하면 위 흐름을 코드에 반영해서, propose(ctx) 시그니처와 pop_urgent(now, die, plane, horizon, earliest_start) 구현(필터링 포함), precheck(..., start_hint) 확장을 한 패치 버전으로 바로 만들어줄게.

## 👤 User

좋아. 흐름은 이해했어. 아래는 지금 코드에서 보완해야 할 사항을 내가 정리한 거야. 니가 보고, 첫번째로 즉시 실행 가능하고 구조 변경점이 큰 것을 우선 순위를 높이고, 코드 전체를 변경해야 하는 risk 가 큰 것을 우선 순위를 낮춰서 정리해줘. 우선 순위가 낮아보이더라도, 필수적으로 개선해야하는 작업은 우선 순위를 높여서 정리하고, 정리된 것을 todo list 로 만들고 스텝 바이 스텝으로 실행하자.
## 보완 사항
- BusySlot 은 왜 만들었는지 확인 필요
- observe_states 에서 특정 die, plane 에 대해서 ratio 구하는 방식이 맞는지 검토 필요
- CFG->op_specs->hooks->states 는 phase 별로 적용할 것을 명시하고 있으나, 일괄적으로 phase 당 3개로 고요해서 단순하게 정리 필요
- addr.observe_states(hook.die, hook.plane) 에서 시간 정보 추가 필요. pgmable_ration, readalbe_ratio, local_plane_frac 등도 현재 시간에 맞춰 업데이트 돼야 한다.
- addr.select(kind, die, plane) 에서 multi-plane operation 시 List[Address] 형태로 반환 필요
- precheck 함수 구현 필요
- obligation 으로 만들어진 operation 은 종류에 따라서 op.targets 에서 연산하는 로직 구현 필요. sequential read/pgm 는 page address + 1 , cache read 의 경우에 순서상 cache read 가 먼저 나오고 dout 이 나중인데, dout 의 address 는 cache read 의 address -1 이다.
- _schedule_operation -> addr.reserve(die,plane,start,end) 시에 operation 에 따라서 erase/pgm/multi-plane operation 의 경우 해당되는 plane 모두 reserve 처리 필요
- policy operation 의 선택은 weight 보다는 정해진 operation.phase 조건 하의 operation 확률 weight 값을 받아와서 random choice 해야함.
- policy 와 obligation 의 우선 순위를 처리하는 로직 구현 필요. 현재는 obl.pop_urget(sch.now) 는 구현없이 조건 obl.heappop 을 하게 돼있음
- op.movable 이 정말 필요한지 검토
- 두 개 이상의 operation 을 순차적으로 예약하는 경우 처리 로직 어느 단계에서 구현할 지 검토. addr.precheck 만으로 충분한지 관점
  - sequential read/program 이 예약된 경우, 동일 block 상에 erase/program/read 는 금지해야 한다.
  - obligation 이 heap 에 여러개 쌓여 있을 경우, 어느 obligation 부터 꺼낼지 로직 구현 필요. 현재는 deadline 기준으로 정렬이 되고, obligation 사이에 dependency check 필요한지 여부 검토 필요
- make_phase_hooks 으로 'PHASE_HOOK' event push 할 떄 pattern time resolution 고려 time sampling 필요
- plane interleave read 를 만들기 위해서, 스케쥴러가 개입을 하는 것이 필요한지 검토
- obligation, policy operation 간에 addr.select 를 쓰고 안쓰고 정해지는데, 통합해야 할 필요성? obligation 은 source  operation 의 targets 상속받는 형식이지만, addr.select 는 샘플링 하는 방식
- make_phase_hooks(op, start, sch.cfg["op_specs"][op.kind.name], die, plane) 에서 op 를 넘겨줌에도 별도로 sch.cfg["op_specs"][op.kind.name] 을 넘겨주고 있다. 통합 가능한지 검토
- sch._schedule_operation 에서 dur 계산 로직 수정 필요. StateSeq, Operation clss 구현과 연결됨. Stateseq.times 는 절대값을 저장하므로 마지막 값만 추출해서 사용가능. 하지만, 현재 구현상 times 에 저장되는 값은 state 의 끝나는 시간이 저장됨. 그리고 operation 이 끝난 후는 state 가 다른 operation 이 수행되기 전까지 무한히 유지되므로 times 의 마지막 값은 10ms 의 매우 큰 값이 저장돼 있음 
  - @dataclass<br>
  class StateSeq<br>
  times: List[float]<br>
  states: List[str]<br>
  id: int<br>

## 🤖 Assistant

좋아. 아래는 **“propose 거절률 최소화”**를 최우선 목표로 한 우선순위화된 To-Do 리스트와, 즉시 적용 가능한 패치 스텝이야.  

원칙: 바로 실행 가능 + 구조적 이득 큰 것 = 상위, 코드 전반 리스크 큰 것 = 하위. 단, 필수 개선은 상위로 끌어올림.

✅ 우선순위별 To-Do
P0 — 지금 적용 (구조 개선 크고, 거절률↓ 효과 큼 / 리스크 낮음)

P1 — 다음 단계 (거절률↓/튜닝력↑, 리스크 보통)

P2 — 이후 (최적화/리포팅, 리스크 낮음)

🛠️ P0 패치 — 즉시 적용 스텝 & 코드 조각
아래 조각은 네가 만든 nandsim_demo.py 기준으로 부분 치환하면 돼.
(1) observe_states/available_at/컨텍스트 → propose 전달
Scheduler (PHASE_HOOK 처리부)

AddressManager.observe_states 시그니처

(2) PolicyEngine.propose 시그니처 확장 + 의무 우선 + precheck
시그니처 변경

의무 우선 + precheck 적용

정책 후보에도 precheck

(3) ObligationManager.pop_urgent 구현(필터링 포함)

(4) AddressManager.precheck + 예약 테이블 + 다중 plane 예약
상태 보관 필드 추가

precheck 구현

reserve 다중 plane 반영 & commit 그대로

(5) observe_states(now) 적용으로 호출부 일관화

▶ 실행 순서 (Step-by-Step)

여기까지가 P0 패치. 이 상태에서 **거절률(=precheck 실패로 스케줄 불가)**이 유의미하게 떨어져야 한다.

(참고) P1에서 바로 붙일 작은 조각
Hook 시간 양자화

duration 래퍼

필요하면 위 패치들을 내가 준 단일 파일 데모에 붙인 통합본으로 만들어 줄 수 있어. 다음 턴에서 원하면 바로 합쳐서 전달할게.

## 👤 User

통합본 만들어줘

## 🤖 Assistant

아래는 P0 패치가 모두 반영된 단일 파일 통합본이에요.

파일명 예: nandsim_demo_p0.py

원하는 대로 바로 실행하면 됩니다:

다음 단계로 가면,

## 👤 User

각 아이템별 risk 평가 후 진행해

## 🤖 Assistant

아래는 보완 항목별 리스크 평가와 바로 진행할 실행 플랜이야.  

원칙: 즉시 효과·구조 이득 큼 = 상위, 광범위 파급·불확실성 큼 = 하위. 단, 필수 개선은 상향 배치.

1) 항목별 리스크 평가

필수 개선: J(의무 타깃 처리), N(연속 예약·의존성 강화) — 위험은 높지만, 시퀀스 정합성에 핵심이라 상향.

2) 실행 플랜 (우선순위·병행 가능성 반영)
Step 1 — 저위험/고효율 안정화 (즉시)

리스크: 낮음 / 효과: 결정적 타이밍 안정, 유지보수성↑
Step 2 — 의무/정책 충돌 최소화 (필수)

리스크: 중 / 효과: 의무 미충족률↓, 분포 제어 용이
Step 3 — 정합성 강화 (필수·고위험 단계)

리스크: 높음 / 완화: 단계별 게이트(READ/PGM만→ERASE확장), 풍부한 단위테스트
Step 4 — 정책·관측 개선 (중위험)

Step 5 — 정리/리포팅

3) 지금 당장 적용할 소규모 패치(요약)
아래 4개는 코드 영향 작고 안정화 효과 큼 — Step 1 즉시 적용:

(호출부에서 cfg 인자 제거)

원하면 위 4개는 현 통합본에 바로 붙인 코드로 넘겨줄게.

4) 단위테스트 체크리스트 (각 단계 완료 기준)

다음 메시지에서 Step 1 패치 적용 통합본을 바로 제공할게. 원하면 **Step 2(의무 부스팅·타깃 어댑터·phase-조건 가중)**까지 묶어서 내줄 수도 있어.

## 👤 User

그 전에 phase-조건 별 확률 분포를 어떻게 구현할 건지 확인하고 넘어가자. 내가 의도한 바는 operation-phase 가 곱해진 조건에서의 확률이야. 예를 들면, { "READ.ISSE": { "READ": 0.0, "PROGRAM": 0.0, ...}, "READ.CORE_BUSY": { ...}, "PROGRAM.ISSUE": {...}, ... } 이런 형태야. 너의 계획과 나의 설명이 맞는지 컨펌해줘.

## 🤖 Assistant

맞아—네가 의도한 “operation×phase(=현재 훅의 연산/스테이트/포지션) 조건에서의 확률로 다음 operation을 샘플링” 방식을 그대로 쓸 거야. 나는 이걸 phase-조건 분포 테이블로 구현하고, 존재하면 그걸 1순위로, 없으면 점진적 백오프로 처리하려 했어.
아래처럼 정리할게.
합의되는 모델

구현 포인트 (간단 패치 스케치)
1) 훅 라벨 파싱

2) 분포 가져오기(백오프 포함)

3) PolicyEngine.propose 내부의 선택 로직 교체

이렇게 하면 **너의 의도(“현재 훅의 OP×STATE(×POS) 조건 분포”)**가 최우선으로 적용되고, 설정이 비어있거나 0합일 때만 기존 가중 곱 방식으로 자연스럽게 내려가.

권장 스키마 (Config에 추가)

주의/테스트

이 구조면 네가 원한 “operation-phase 곱 조건 분포”가 그대로 반영돼. 원하면 지금 통합본에 이 로직을 바로 붙여서 업데이트해줄게.

## 👤 User

업데이트 해줘.

## 🤖 Assistant

아래는 phase-조건 분포(현재 훅의 OP.STATE(.POS) 조건에서 다음 operation 확률을 직접 지정) 를 통합한 업데이트 버전 단일 파일입니다.

파일명 예: nandsim_demo_p0_phase.py

메모

원하면 여기에 시간 양자화(quantize), duration 래퍼, fanout/interleave 옵션까지 이어서 묶어줄게.

## 👤 User

묶어서 제공해줘.

## 🤖 Assistant

좋아—요청한 항목을 한 파일에 모두 묶은 통합본이야.  

포함된 개선사항:
- Phase-조건 분포(훅 라벨 OP.STATE(.POS) → 다음 op 확률)
- 시간 양자화(SIM_RES_US, quantize)
- duration 래퍼(get_op_duration)
- multi-plane fanout / interleave (config 기반, AddressManager.select(..., fanout, interleave))

파일명 예: nandsim_demo_p1_bundle.py

```python
# nandsim_demo_p1_bundle.py — Phase-conditional + Quantize + Duration wrapper + Multi-plane fanout/interleave
# Stdlib only.

from __future__ import annotations
import heapq, random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple, Set

# ---------------------------- Simulation Resolution ----------------------------
SIM_RES_US = 0.01
def quantize(t: float) -> float:
    return round(t / SIM_RES_US) * SIM_RES_US

# ---------------------------- Config ----------------------------

CFG = {
    "rng_seed": 12345,
    "policy": {
        "queue_refill_period_us": 3.0,
        "lookahead_k": 4,
        "run_until_us": 180.0,
    },
    # backoff weights (phase-conditional 미존재/0합일 때 사용)
    "weights": {
        "base": {"host": {"READ": 0.85, "PROGRAM": 0.10, "ERASE": 0.05, "SR": 0.00, "RESET": 0.00, "DOUT": 0.00}},
        "g_state": {"pgmable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.8},
                    "readable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.9}},
        "g_local": {"plane_busy_frac": {"low": 1.2, "mid": 1.0, "high": 0.9}},
        "g_phase": {
            "READ": {"START_NEAR": 1.2, "MID_NEAR": 0.9, "END_NEAR": 1.2},
            "PROGRAM": {"START_NEAR": 1.1, "MID_NEAR": 1.0, "END_NEAR": 0.9},
            "ERASE": {"START_NEAR": 0.9, "MID_NEAR": 1.1, "END_NEAR": 1.1},
            "SR": {"START_NEAR": 1.0, "MID_NEAR": 1.0, "END_NEAR": 1.0},
            "RESET": {"START_NEAR": 1.0, "MID_NEAR": 1.0, "END_NEAR": 1.0},
        },
    },
    "mixture": {"classes": {"host": 1.0}},  # demo: host only
    # 현재 훅 컨텍스트(OP.STATE 또는 OP.STATE.POS) → 다음 op 확률
    "phase_conditional": {
        "READ.ISSUE":              {"READ": 0.00, "PROGRAM": 0.80, "ERASE": 0.20},
        "READ.CORE_BUSY.START":    {"READ": 0.05, "PROGRAM": 0.85, "ERASE": 0.10},
        "READ.CORE_BUSY.MID":      {"READ": 0.10, "PROGRAM": 0.70, "ERASE": 0.20},
        "READ.DATA_OUT.START":     {"READ": 0.60, "PROGRAM": 0.25, "ERASE": 0.15},
        "PROGRAM.ISSUE":           {"READ": 0.50, "PROGRAM": 0.25, "ERASE": 0.25},
        "PROGRAM.CORE_BUSY.END":   {"READ": 0.60, "PROGRAM": 0.10, "ERASE": 0.30},
        "DEFAULT":                 {"READ": 0.60, "PROGRAM": 0.30, "ERASE": 0.10},
    },
    # fanout / interleave 정책 (phase별 override 지원)
    "selection": {
        "defaults": {
            "READ":    {"fanout": 1, "interleave": True},
            "PROGRAM": {"fanout": 1, "interleave": True},
            "ERASE":   {"fanout": 1, "interleave": False},
        },
        # OP.STATE(.POS) → {fanout, interleave} override
        "phase_overrides": {
            "READ.CORE_BUSY.START": {"fanout": 2, "interleave": True},  # 예: CORE_BUSY 시작엔 2-plane READ
        }
    },
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
    "topology": {"dies": 1, "planes_per_die": 4, "blocks_per_plane": 8, "pages_per_block": 16},
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

def _addr_str(a: Address)->str:
    return f"(d{a.die},p{a.plane},b{a.block},pg{a.page})"

# ---------------------------- Utility ----------------------------

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

def parse_hook_key(label: str):
    # e.g., "READ.CORE_BUSY.START" -> ("READ","CORE_BUSY","START")
    parts = label.split(".")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], None
    return None, None, None  # BOOT/REFILL 등 비정형 훅

def get_phase_dist(cfg: Dict[str,Any], hook_label: str):
    op, state, pos = parse_hook_key(hook_label)
    pc = cfg.get("phase_conditional", {})
    candidates = []
    if op and state and pos: candidates.append(f"{op}.{state}.{pos}")
    if op and state:         candidates.append(f"{op}.{state}")
    candidates.append("DEFAULT")
    for key in candidates:
        dist = pc.get(key)
        if dist and sum(dist.values()) > 0:
            return dist, key
    return None, None

def get_phase_selection_override(cfg: Dict[str,Any], hook_label: str, kind_name: str):
    # fanout/interleave 결정: phase override > defaults
    op, state, pos = parse_hook_key(hook_label)
    po = cfg.get("selection", {}).get("phase_overrides", {})
    keys = []
    if op and state and pos: keys.append(f"{op}.{state}.{pos}")
    if op and state:         keys.append(f"{op}.{state}")
    for k in keys:
        val = po.get(k)
        if val:
            f = int(val.get("fanout", 1))
            iv = bool(val.get("interleave", True))
            return f, iv
    dflt = cfg.get("selection", {}).get("defaults", {}).get(kind_name, {"fanout":1,"interleave":True})
    return int(dflt.get("fanout",1)), bool(dflt.get("interleave", True))

def get_op_duration(op: Operation) -> float:
    # wrapper: 현재는 상대 duration 합, 추후 StateSeq.times(절대)로 대체 가능
    return sum(seg.dur_us for seg in op.states)

# ---------------------------- OpSpec: build ops & hooks ----------------------------

def build_operation(kind: OpKind, cfg_op: Dict[str, Any], targets: List[Address]) -> Operation:
    states=[]
    for s in cfg_op["states"]:
        dur = sample_dist(s["dist"])
        states.append(StateSeg(name=s["name"], dur_us=dur))
    return Operation(kind=kind, targets=targets, states=states)

def make_phase_hooks(cfg: Dict[str,Any], op: Operation, start_us: float, die:int, plane:int) -> List[PhaseHook]:
    cfg_op = cfg["op_specs"][op.kind.name]
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
                hooks.append(PhaseHook(time_us=quantize(seg_start + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.START", die=die, plane=plane))
            elif rule["when"]=="STATE_MID":
                hooks.append(PhaseHook(time_us=quantize(seg_mid   + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.MID",   die=die, plane=plane))
            elif rule["when"]=="STATE_END":
                hooks.append(PhaseHook(time_us=quantize(seg_end   + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.END",   die=die, plane=plane))
        cur = seg_end
    return hooks

# ---------------------------- Address Manager ----------------------------

class AddressManager:
    def __init__(self, cfg: Dict[str, Any]):
        topo = cfg["topology"]
        self.cfg = cfg
        self.dies = topo["dies"]
        self.planes = topo["planes_per_die"]
        self.pages_per_block = topo["pages_per_block"]
        self.blocks_per_plane = topo["blocks_per_plane"]

        self.available: Dict[Tuple[int,int], float] = {(0,p): 0.0 for p in range(self.planes)}
        self.cursors: Dict[Tuple[int,int], List[int]] = {(0,p): [0,0,0] for p in range(self.planes)}  # [block, next_pgm_page, next_rd_page]
        self.programmed: Dict[Tuple[int,int], Set[Tuple[int,int]]] = {(0,p): set() for p in range(self.planes)}
        self.resv: Dict[Tuple[int,int], List[Tuple[float,float,Optional[int]]]] = {(0,p): [] for p in range(self.planes)}

    # ---- observation / selection / checks ----

    def available_at(self, die:int, plane:int) -> float:
        return self.available[(die,plane)]

    def observe_states(self, die:int, plane:int, now_us: float):
        prog = len(self.programmed[(die,plane)])
        pgmable_ratio  = "mid" if prog < 10 else "low"
        readable_ratio = "mid" if prog > 0 else "low"
        plane_busy_frac = "high" if self.available_at(die,plane) > now_us else "low"
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls": "host"},
                {"plane_busy_frac": plane_busy_frac})

    def _plane_ring(self, start_plane:int, fanout:int, interleave:bool) -> List[int]:
        # interleave=True면 start_plane부터 시계방향으로 fanout개 선택
        if fanout <= 1:
            return [start_plane]
        ring = []
        P = self.planes
        idx = start_plane
        for _ in range(fanout):
            ring.append(idx)
            idx = (idx + 1) % P  # 간단 인접 분산
        return ring

    def select(self, kind: OpKind, die:int, plane:int, fanout:int=1, interleave:bool=True) -> List[Address]:
        planes = self._plane_ring(plane, fanout, interleave)
        targets: List[Address] = []
        for pl in planes:
            if kind == OpKind.READ:
                prog = sorted(self.programmed[(die,pl)])
                tgt = prog[0] if prog else (0,0)
                targets.append(Address(die, pl, block=tgt[0], page=tgt[1]))
            elif kind == OpKind.DOUT:
                raise RuntimeError("DOUT selection must be provided by obligation targets")
            elif kind == OpKind.PROGRAM:
                b, pgm_p, _ = self.cursors[(die,pl)]
                targets.append(Address(die, pl, block=b, page=pgm_p))
                # advance cursor
                pgm_p += 1
                if pgm_p >= self.pages_per_block:
                    pgm_p = 0
                    b = (b+1) % self.blocks_per_plane
                self.cursors[(die,pl)] = [b, pgm_p, self.cursors[(die,pl)][2]]
            elif kind == OpKind.ERASE:
                b = self.cursors[(die,pl)][0]
                targets.append(Address(die, pl, block=b, page=None))
            else:
                targets.append(Address(die, pl, block=0, page=0))
        return targets

    def precheck(self, kind: OpKind, targets: List[Address], start_hint: float) -> bool:
        """Minimal feasibility check: time overlap + simple block rule placeholder."""
        start_hint = quantize(start_hint)
        end_hint = start_hint  # guard 0 for now
        seen_planes = set()
        for t in targets:
            key = (t.die, t.plane)
            if key in seen_planes: 
                continue
            seen_planes.add(key)
            for (s,e,_) in self.resv[key]:
                if not (end_hint <= s or e <= start_hint):
                    return False
        # TODO: extend with scope rules (block/page conflicts)
        return True

    def reserve(self, die:int, plane:int, start:float, end:float, block: Optional[int]=None):
        start = quantize(start); end = quantize(end)
        self.available[(die,plane)] = max(self.available[(die,plane)], end)
        self.resv[(die,plane)].append((start, end, block))

    def commit(self, op: Operation):
        t = op.targets[0]
        key = (t.die, t.plane)
        if op.kind == OpKind.PROGRAM and t.page is not None:
            self.programmed[key].add((t.block, t.page))
        elif op.kind == OpKind.ERASE:
            self.programmed[key] = {pp for pp in self.programmed[key] if pp[0] != t.block}

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
                    deadline_us = quantize(now_us + dt),
                    boost_factor = spec["priority_boost"]["boost_factor"],
                    hard_slot = spec["priority_boost"].get("hard_slot", False),
                )
                heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._seq, ob=ob))
                self._seq += 1
                print(f"[{now_us:7.2f} us] OBLIG  created: {op.kind.name} -> {ob.require.name} by {ob.deadline_us:7.2f} us, target={_addr_str(ob.targets[0])}")

    def pop_urgent(self, now_us: float, die:int, plane:int,
                   horizon_us: float, earliest_start: float) -> Optional[Obligation]:
        """Earliest-deadline obligation for this (die,plane) feasible at earliest_start."""
        if not self.heap: 
            return None
        kept: List[_ObHeapItem] = []
        chosen: Optional[Obligation] = None
        now_us = quantize(now_us)
        earliest_start = quantize(earliest_start)
        while self.heap and not chosen:
            item = heapq.heappop(self.heap)
            ob = item.ob
            tgt = ob.targets[0]
            same_plane = (tgt.die == die and tgt.plane == plane)
            in_horizon = ((ob.deadline_us - now_us) <= max(horizon_us, 0.0)) or ob.hard_slot
            feasible_time = (earliest_start <= ob.deadline_us)
            if same_plane and in_horizon and feasible_time:
                chosen = ob
                break
            kept.append(item)
        for it in kept:
            heapq.heappush(self.heap, it)
        return chosen

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

    def _roulette(self, dist: Dict[str, float], allow: set) -> Optional[str]:
        items = [(name, p) for name, p in dist.items() if name in allow and p > 0.0]
        if not items: 
            return None
        total = sum(p for _, p in items)
        r = random.random() * total
        acc = 0.0
        pick = items[-1][0]
        for name, p in items:
            acc += p
            if r <= acc:
                pick = name; break
        return pick

    def propose(self, now_us: float, hook: PhaseHook,
                global_state: Dict[str,str], local_state: Dict[str,str],
                earliest_start: float) -> Optional[Operation]:
        # 0) Obligation 우선 (plane/time 필터)
        ob = self.obl.pop_urgent(now_us, hook.die, hook.plane,
                                 horizon_us=10.0, earliest_start=earliest_start)
        if ob:
            if self.addr.precheck(ob.require, ob.targets, start_hint=earliest_start):
                cfg_op = self.cfg["op_specs"][ob.require.name]
                op = build_operation(ob.require, cfg_op, ob.targets)
                op.meta["source"]="obligation"
                op.meta["phase_key_used"]="(obligation)"
                return op

        # 1) Phase-conditional 분포 시도
        allow = set(self.cfg["op_specs"].keys()) - {"DOUT"}  # DOUT은 의무 기본
        dist, used_key = get_phase_dist(self.cfg, hook.label)
        fanout, interleave = get_phase_selection_override(self.cfg, hook.label, "<DEFAULT>")
        if dist:
            pick = self._roulette(dist, allow)
            if pick:
                fanout, interleave = get_phase_selection_override(self.cfg, hook.label, pick)
                kind = OpKind[pick]
                targets = self.addr.select(kind, hook.die, hook.plane, fanout=fanout, interleave=interleave)
                if targets and self.addr.precheck(kind, targets, start_hint=earliest_start):
                    op = build_operation(kind, self.cfg["op_specs"][pick], targets)
                    op.meta["source"]="policy.phase_conditional"
                    op.meta["phase_key_used"]=used_key
                    op.meta["fanout"]=fanout; op.meta["interleave"]=interleave
                    return op
            # 실패 시 score 백오프

        # 2) Backoff: 기존 곱셈 가중(score)
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
        fanout, interleave = get_phase_selection_override(self.cfg, hook.label, pick)
        targets = self.addr.select(kind, hook.die, hook.plane, fanout=fanout, interleave=interleave)
        if not targets:
            return None
        if not self.addr.precheck(kind, targets, start_hint=earliest_start):
            return None
        op = build_operation(kind, self.cfg["op_specs"][pick], targets)
        op.meta["source"]="policy.score_backoff"
        op.meta["phase_key_used"]="(score_backoff)"
        op.meta["fanout"]=fanout; op.meta["interleave"]=interleave
        return op

# ---------------------------- Scheduler ----------------------------

class Scheduler:
    def __init__(self, cfg, addr_mgr: AddressManager, policy_engine: PolicyEngine, obl_mgr: ObligationManager):
        self.cfg=cfg; self.addr=addr_mgr; self.SPE=policy_engine; self.obl=obl_mgr
        self.now=0.0
        self.ev=[]  # (time, seq, type, payload)
        self._seq=0
        self.stat_propose_calls = 0
        self.stat_scheduled = 0
        self._push(0.0, "QUEUE_REFILL", None)
        # Bootstrap hooks per plane
        for plane in range(self.addr.planes):
            self._push(0.0, "PHASE_HOOK", PhaseHook(0.0, "BOOT.START", 0, plane))

    def _push(self, t: float, typ: str, payload: Any):
        t = quantize(t)
        heapq.heappush(self.ev, (t, self._seq, typ, payload)); self._seq+=1

    def _unique_plane_keys(self, targets: List[Address]) -> List[Tuple[int,int,int]]:
        s=set()
        for a in targets:
            s.add((a.die, a.plane, a.block))
        return list(s)

    def _start_time_for_targets(self, targets: List[Address]) -> float:
        planes = {(a.die, a.plane) for a in targets}
        avail = [self.addr.available_at(d,p) for (d,p) in planes]
        return quantize(max([self.now] + avail))

    def _schedule_operation(self, op: Operation):
        start = self._start_time_for_targets(op.targets)
        dur = get_op_duration(op)
        end  = quantize(start + dur)
        # reserve for all involved planes
        for d,p,b in self._unique_plane_keys(op.targets):
            self.addr.reserve(d, p, start, end, block=b)
        # events
        self._push(start, "OP_START", op)
        self._push(end,   "OP_END",   op)
        # hooks for each involved plane
        for d,p,_ in self._unique_plane_keys(op.targets):
            hooks = make_phase_hooks(self.cfg, op, start, d, p)
            for h in hooks:
                self._push(h.time_us, "PHASE_HOOK", h)
        first = op.targets[0]
        print(f"[{self.now:7.2f} us] SCHED  {op.kind.name:7s} tgt={len(op.targets)} start={start:7.2f} end={end:7.2f} 1st={_addr_str(first)} src={op.meta.get('source')} key={op.meta.get('phase_key_used')} fanout={op.meta.get('fanout')}")

        self.stat_scheduled += 1

    def run_until(self, t_end: float):
        t_end = quantize(t_end)
        while self.ev and self.ev[0][0] <= t_end:
            self.now, _, typ, payload = heapq.heappop(self.ev)
            if typ=="QUEUE_REFILL":
                for plane in range(self.addr.planes):
                    self._push(self.now, "PHASE_HOOK", PhaseHook(self.now, "REFILL.NUDGE", 0, plane))
                nxt = quantize(self.now + self.cfg["policy"]["queue_refill_period_us"])
                self._push(nxt, "QUEUE_REFILL", None)

            elif typ=="PHASE_HOOK":
                hook: PhaseHook = payload
                earliest_start = self.addr.available_at(hook.die, hook.plane)
                global_state, local_state = self.addr.observe_states(hook.die, hook.plane, self.now)
                self.stat_propose_calls += 1
                op = self.SPE.propose(self.now, hook, global_state, local_state, earliest_start)
                if op:
                    self._schedule_operation(op)

            elif typ=="OP_START":
                op: Operation = payload
                first = op.targets[0]
                print(f"[{self.now:7.2f} us] START  {op.kind.name:7s} target={_addr_str(first)}")

            elif typ=="OP_END":
                op: Operation = payload
                first = op.targets[0]
                print(f"[{self.now:7.2f} us] END    {op.kind.name:7s} target={_addr_str(first)}")
                self.addr.commit(op)
                self.obl.on_commit(op, self.now)

        print(f"\n=== Stats ===")
        print(f"propose calls : {self.stat_propose_calls}")
        print(f"scheduled ops : {self.stat_scheduled}")
        if self.stat_propose_calls:
            rate = 100.0 * self.stat_scheduled / self.stat_propose_calls
            print(f"accept ratio  : {rate:.1f}%")

# ---------------------------- Main ----------------------------

def main():
    random.seed(CFG["rng_seed"])
    addr = AddressManager(CFG)
    obl  = ObligationManager(CFG["obligations"])
    spe  = PolicyEngine(CFG, addr, obl)
    sch  = Scheduler(CFG, addr, spe, obl)
    run_until = CFG["policy"]["run_until_us"]
    print("=== NAND Sequence Generator Demo (Bundled: phase+quantize+fanout) ===")
    sch.run_until(run_until)
    print("=== Done ===")

if __name__ == "__main__":
    main()

```

사용 방법

```bash
python3 nandsim_demo_p1_bundle.py

```

무엇이 달라졌나 (짧게)
- CFG["phase_conditional"]로 훅 컨텍스트 기반 확률을 최우선 적용, 실패 시 기존 가중(score)로 백오프.
- CFG["selection"]의 defaults와 phase_overrides로 fanout/interleave 제어.
- 이벤트/훅/커밋/의무 데드라인에 시간 양자화 적용으로 타이밍 안정성↑.
- _schedule_operation은 get_op_duration을 사용, 훗날 StateSeq.times로 교체 가능.

다음으로 진행할 때는:
- 의무 타깃 변환 로직(sequential/cached read에 따른 주소 오프셋)과
- 블록/페이지 스코프 의존성 룰(ERASE↔PGM/READ, PGM↔READ 동시 금지)
을 붙이면 정합성이 한층 올라가. 원하면 그 두 가지도 바로 패치본으로 이어서 내줄게.

## 👤 User

=== Stats ===
propose calls : 376
scheduled ops : 370
accept ratio  : 98.4%
=== Done ===
accept ration 가 좀 떨어지긴 했지만, 아직까진 좋아. 
두 가지 진행하기 전에 리뷰하고 가자. 우선 의무 타깃 변환 로직의 대상에 대해 정하고 가자. Sequential read 란, host 의 요청에 의해서 여러 page 를 순차적으로 읽는 동작이야. 예를 들어 20 page 를 순차적으로 읽을 수 있어. 방법 중 하나는 obligation heap 에 모두 push 해 두는 거야. 이 방식의 risk 는 중간에 해당 block 을 policy 에 의해서 erase/program/read 못하는 상황이 발생하는거야. 내 목적은 최대한 host 의 요청을 들어주면서 policy 제안도 할 수 있는 방식이야. 그러기 위해선 obligation 과 policy 의 우선 순위를 조정해야 되겠지. 이처럼 가능한 방법들과, 그것들의 risk 를 정리해봐.

## 🤖 Assistant

좋아, “Host의 순차 READ(예: 20pages)”를 최대한 지켜주면서도 정책 제안(프로그램/GC 등)도 하려면, 의무 생성·우선순위·락(예약) 전략을 함께 설계해야 해. 아래에 가능한 방법들 + 리스크를 정리하고, 권장안까지 제시할게.

옵션별 설계 & 리스크
1) 전량 사전발행 (Push-all)
설명: Host가 20page 요청 → 20개의 READ 의무(그리고 각 READ→DOUT 의무)가 한 번에 heap에 들어감.
- 장점
  - 구현 단순. 다음 타깃 페이지는 항상 page += 1.
  - 스케줄러가 미리 훅을 깔아 빠르게 소화 가능.
- 리스크
  - 블록 경합/정합성: 같은 블록에 대해 GC(ERASE)나 PROGRAM이 필요해도 오랫동안 막힘.
  - heap 팽창: 대량 의무 큐에 의해 우선순위 정렬·스캔 비용↑.
  - 장시간 락 필요: 중간에 정책이 블록을 사용하면 의무 불가 → deadline miss.
- 완화
  - 블록 하드락(ERASE/PROGRAM 금지) + TTL 설정…but 정책 자유도↓, GC 기아 가능.

2) 슬라이딩 윈도(lookahead W) 발행  ✅ 권장
설명: 항상 W개의 다음 페이지 READ 의무만 outstanding. 각 READ/DOUT가 끝날 때 다음 페이지 의무를 보충(refill).
- 장점
  - 충돌 표면적↓: 한꺼번에 20개 잠그지 않음.
  - 응답성/유연성 균형: Host는 연속 처리되고, 정책도 틈새에 개입 가능.
  - 적응형: W를 시스템 상태(훅 밀도, plane busy, GC 수위)에 맞춰 동적으로 조절 가능.
- 리스크
  - 윈도 경계에서 head-of-line이 GC와 맞부딪히면 잠시 지연.
  - refill 타이밍이 늦으면 순간 throughput dip.
- 완화
  - deadline 부스팅: 윈도 내 가장 앞(page k)의 READ가 마감 임박이면 hard-slot 허용.
  - soft block-lock(하드락 대신): 해당 블록에 ERASE/PROGRAM은 우선순위 낮춤 + 일정 시간 지연, 응급 GC만 예외.

3) JIT 의무 생성 (Just-in-time)
설명: OP_END/특정 훅에서 다음 한 개만 의무 생성. 미리 쌓지 않음.
- 장점
  - 충돌 최소. heap 작음.
- 리스크
  - 지연: 훅 타이밍/분포에 민감 → 연속 READ의 latency 스파이크.
  - throughput 저하 위험.
- 완화
  - phase-conditional에서 READ 비중을 START/END에 올려 훅이 오면 곧바로 READ가 나오게 조정. 그래도 ②보다 약함.

4) 하이브리드: 슬라이딩 윈도 + 소프트 락 + 응급 GC 예외  ✅ 권장
설명: ②를 기본으로, **블록 소프트락(만료 TTL)**을 두고, 시스템 수위(예: pgmable_ratio가 low 임계 아래)면 응급 GC가 예외적으로 침투 가능(단, 윈도 경계/페이지 전환 시점에만).
- 장점
  - Host 만족도 유지 + GC 데드락 방지.
- 리스크
  - 정책 복잡도↑. 우선순위/예외 규칙이 명확해야 함.
- 완화
  - 명시적 우선순위표와 임계치 기반 페일세이프 도입(아래 권장 규칙 참고).

의무 vs 정책 우선순위 규칙(권장)
1. Host 순차 READ 의무(READ, DOUT)
  - deadline - now <= boost_window → hard-slot (precheck는 유지).
  - soft-lock된 블록에 대해 ERASE/PROGRAM은 deny 또는 강등.
2. Host 단발성 요청(비순차) / 필수 유지 READ 정책
  - 위 1 다음.
3. 정상 GC/PROGRAM 정책
  - soft-lock 블록 회피. 다른 블록에서 수행.
4. 응급 GC(워터마크 붕괴)
  - 순차 윈도 경계(다음 페이지로 이동하거나 윈도 refill 직전)에서만 침투 허용.
  - 침투 시에도 현재 진행 중 READ page는 완료 보장.

정량 예:
- boost_window = 2.5us, hard_slot=True (현재 데모 값 유지)
- pgmable_ratio < 0.05 → 응급 GC 모드 진입

의무 타깃 변환 로직의 “대상”과 규칙
A) Sequential READ (기본)
- 대상: (die, plane, block, page_start..page_end)의 연속 페이지 범위
- 타깃 함수:
  - next_page(p) = p + 1
  - 윈도 생성: W개 → page = head .. head+W-1 각각에 READ 의무 생성
  - 각 READ 종료 시 DOUT 의무 생성(window_us 분포)

B) Cache READ + DOUT 페어 (옵션)
- 규칙: “cache read가 먼저, 그 후 dout. DOUT의 address = cache read의 address - 1”
- 구현 방식:
  - READ(page p) 커밋 시:
    - 지연 없이 **CACHE_READ(page p+1)**를 정책/의무 중 하나로 즉시 제안 (동일 plane)
    - 동시에 DOUT(page p) 의무 생성
  - 스케줄러는 **CACHE_READ(p+1)**와 **DOUT(p)**의 상대 순서를 보장(precheck에 페이지 스코프 룰 추가: 동일 스트림에서 CACHE_READ(p+1)이 DOUT(p)보다 항상 앞서도록).

C) 금지 룰(의존성)
- 블록 스코프: soft-lock 동안 ERASE, PROGRAM 금지/강등
- 페이지 스코프: 동일 블록에서 PROGRAM ↔ READ 동시 금지, READ 순서 역전 금지
- 응급 GC 예외는 페이지 경계에서만 허용

권장 안 (실행 계획)
1) 슬라이딩 윈도 + 소프트락 + 응급 GC 예외 (옵션 4)
- SeqReadContext(ObligationManager 내부) 도입:
  - die, plane, block, start_page, total_pages, next_page, window=W, ttl_us
  - active_pages: outstanding 의무 페이지 집합
- 소프트락(AddressManager.read_seq_locks[(die,plane,block)] = expiry_us)
  - precheck()에서 해당 블록 ERASE/PROGRAM을 deny/강등
  - expiry_us는 마지막 의무 deadline + grace
  - 응급 GC는 pgmable_ratio < low_watermark일 때, 윈도 경계에서만 예외 허용
- refill 로직:
  - on_commit(READ p) → DOUT(p) 의무 생성 + active_pages에서 제거
  - 빈 슬롯 만큼 next pages 의무 추가(윈도 유지)
  - on_commit(DOUT p) → 완결 카운트 증가
  - 종료 조건: next_page >= start_page+total_pages & active_pages=∅ → 락 해제

2) 우선순위/부스팅
- pop_urgent()는 같은 plane에서 SeqReadContext가 있는 의무를 먼저 스캔
- deadline - now <= boost_window 또는 hard_slot=True면 즉시 반환
- 정책 경로에서는 해당 plane의 phase-conditional을 READ 쪽으로 이동(override)

3) precheck 강화(필수 부분만)
- 블록 스코프: soft-lock 존재 시 ERASE/PROGRAM 금지/강등
- 페이지 스코프: 같은 스트림에서 READ(p+1)은 선행 DOUT(p) 이후만 허용(또는 최소한 예약 시 순서 보장)

리스크 요약 표

| 방법                 | Host 만족  | 정책 자유도 | 구현 복잡도 | 주요 리스크                        |
| -------------------- | ---------- | ----------- | ----------- | ---------------------------------- |
| 전량 사전발행        | 높음(초기) | 낮음        | 낮음        | GC/PROGRAM 장기 차단, heap 팽창    |
| JIT                  | 중~낮음    | 높음        | 낮음        | 훅 정합성 의존 → latency spike     |
| 슬라이딩 윈도        | 높음       | 중          | 중          | 윈도 경계 충돌 시 잠시 지연        |
| 윈도+소프트락+응급GC | 높음       | 중~높음     | 중          | 규칙/예외 복잡, 파라미터 튜닝 필요 |

바로 적용할 구체 변경(현재 코드 기준)
1. ObligationManager에 SeqReadContext 추가
  - start_seq_read(die, plane, block, page0, count, W)
  - refill(now) / on_commit(op, now)에서 윈도 유지
  - read_seq_locks[(d,p,b)] = expiry 갱신
2. AddressManager.precheck
  - if read_seq_locks.get((die,plane,block),0) > start_hint: → ERASE/PROGRAM 거절(응급 GC 모드면 예외)
3. ObligationManager.pop_urgent
  - 같은 plane에서 SeqReadContext 의무 우선
  - deadline 부스팅/hard-slot 처리
4. PolicyEngine
  - 해당 plane이 활성 SeqReadContext면 phase-conditional override로 READ weight ↑
  - cache read 모드일 때 READ(p) 커밋 → CACHE_READ(p+1) 우선 제안(DOUT는 의무)
5. Scheduler (변경 최소)
  - 그대로: propose→precheck→reserve→hooks
  - 응급 GC 진입/해제는 global_state(pgmable_ratio)로 판단

원하면, 위 권장안(옵션 4)을 바탕으로 SeqReadContext + 소프트락 + 윈도 refill이 포함된 패치본을 바로 만들어 줄게. 이 때 W, boost_window, TTL, 응급 GC 임계는 CFG로 노출하고 기본값은 보수적으로 잡을게.

## 👤 User

좋아. 진행해.

## 🤖 Assistant

좋아—말한 슬라이딩 윈도우 순차 READ + 소프트 락 + 응급 GC 예외를 통합한 단일 파일을 드릴게.  

핵심 변화:
- ObligationManager에 SeqReadContext 추가: host의 순차 READ를 윈도 W로 outstanding 유지하며, READ 커밋 시 DOUT 발행 + 리필.
- AddressManager에 soft lock(블록 단위)과 허용 페이지 집합(allowed_pages) 관리, 응급 GC 플래그(per plane).
- precheck가 soft lock 중엔 ERASE/PROGRAM 거절(응급 GC면 예외), READ는 허용 페이지만 통과.
- pop_urgent가 순차 READ 의무를 우선 선택. (plane/시간 필터 유지)
- Scheduler가 훅마다 가용 상태를 보고 응급 GC 모드를 토글.
- 데모를 위해 main()에서 순차 READ 20페이지를 시드(윈도 W=4).

파일명 예: nandsim_demo_p2_seq.py

```python
# nandsim_demo_p2_seq.py — Sequential READ window + soft-lock + emergency GC + phase-conditional etc.
# Stdlib only.

from __future__ import annotations
import heapq, random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Dict, Any, Optional, Tuple, Set

# ---------------------------- Simulation Resolution ----------------------------
SIM_RES_US = 0.01
def quantize(t: float) -> float:
    return round(t / SIM_RES_US) * SIM_RES_US

# ---------------------------- Config ----------------------------

CFG = {
    "rng_seed": 12345,
    "policy": {
        "queue_refill_period_us": 3.0,
        "lookahead_k": 4,
        "run_until_us": 220.0,
    },
    "seq_read": {
        "window": 4,                 # outstanding READ obligations
        "deadline_spacing_us": 2.0,  # spacing between READ deadlines inside window
        "softlock_grace_us": 8.0,    # soft-lock TTL extension after last outstanding deadline
        "boost_window_us": 2.5,      # pop_urgent boost horizon for seq READ (hard-slot)
    },
    # backoff weights (phase-conditional 미존재/0합일 때 사용)
    "weights": {
        "base": {"host": {"READ": 0.85, "PROGRAM": 0.10, "ERASE": 0.05, "SR": 0.00, "RESET": 0.00, "DOUT": 0.00}},
        "g_state": {"pgmable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.8},
                    "readable_ratio": {"low": 1.3, "mid": 1.0, "high": 0.9}},
        "g_local": {"plane_busy_frac": {"low": 1.2, "mid": 1.0, "high": 0.9}},
        "g_phase": {
            "READ": {"START_NEAR": 1.2, "MID_NEAR": 0.9, "END_NEAR": 1.2},
            "PROGRAM": {"START_NEAR": 1.1, "MID_NEAR": 1.0, "END_NEAR": 0.9},
            "ERASE": {"START_NEAR": 0.9, "MID_NEAR": 1.1, "END_NEAR": 1.1},
            "SR": {"START_NEAR": 1.0, "MID_NEAR": 1.0, "END_NEAR": 1.0},
            "RESET": {"START_NEAR": 1.0, "MID_NEAR": 1.0, "END_NEAR": 1.0},
        },
    },
    "phase_conditional": {
        "READ.ISSUE":              {"READ": 0.00, "PROGRAM": 0.80, "ERASE": 0.20},
        "READ.CORE_BUSY.START":    {"READ": 0.05, "PROGRAM": 0.85, "ERASE": 0.10},
        "READ.CORE_BUSY.MID":      {"READ": 0.10, "PROGRAM": 0.70, "ERASE": 0.20},
        "READ.DATA_OUT.START":     {"READ": 0.60, "PROGRAM": 0.25, "ERASE": 0.15},
        "PROGRAM.ISSUE":           {"READ": 0.50, "PROGRAM": 0.25, "ERASE": 0.25},
        "PROGRAM.CORE_BUSY.END":   {"READ": 0.60, "PROGRAM": 0.10, "ERASE": 0.30},
        "DEFAULT":                 {"READ": 0.60, "PROGRAM": 0.30, "ERASE": 0.10},
    },
    "selection": {
        "defaults": {
            "READ":    {"fanout": 1, "interleave": True},
            "PROGRAM": {"fanout": 1, "interleave": True},
            "ERASE":   {"fanout": 1, "interleave": False},
        },
        "phase_overrides": {
            "READ.CORE_BUSY.START": {"fanout": 2, "interleave": True},
        }
    },
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
    "topology": {"dies": 1, "planes_per_die": 4, "blocks_per_plane": 8, "pages_per_block": 16},
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

def _addr_str(a: Address)->str:
    return f"(d{a.die},p{a.plane},b{a.block},pg{a.page})"

# ---------------------------- Utility ----------------------------

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

def parse_hook_key(label: str):
    parts = label.split(".")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], None
    return None, None, None

def get_phase_dist(cfg: Dict[str,Any], hook_label: str):
    op, state, pos = parse_hook_key(hook_label)
    pc = cfg.get("phase_conditional", {})
    candidates = []
    if op and state and pos: candidates.append(f"{op}.{state}.{pos}")
    if op and state:         candidates.append(f"{op}.{state}")
    candidates.append("DEFAULT")
    for key in candidates:
        dist = pc.get(key)
        if dist and sum(dist.values()) > 0:
            return dist, key
    return None, None

def get_phase_selection_override(cfg: Dict[str,Any], hook_label: str, kind_name: str):
    op, state, pos = parse_hook_key(hook_label)
    po = cfg.get("selection", {}).get("phase_overrides", {})
    keys = []
    if op and state and pos: keys.append(f"{op}.{state}.{pos}")
    if op and state:         keys.append(f"{op}.{state}")
    for k in keys:
        val = po.get(k)
        if val:
            f = int(val.get("fanout", 1))
            iv = bool(val.get("interleave", True))
            return f, iv
    dflt = cfg.get("selection", {}).get("defaults", {}).get(kind_name, {"fanout":1,"interleave":True})
    return int(dflt.get("fanout",1)), bool(dflt.get("interleave", True))

def get_op_duration(op: Operation) -> float:
    return sum(seg.dur_us for seg in op.states)

# ---------------------------- OpSpec: build ops & hooks ----------------------------

def build_operation(kind: OpKind, cfg_op: Dict[str, Any], targets: List[Address]) -> Operation:
    states=[]
    for s in cfg_op["states"]:
        dur = sample_dist(s["dist"])
        states.append(StateSeg(name=s["name"], dur_us=dur))
    return Operation(kind=kind, targets=targets, states=states)

def make_phase_hooks(cfg: Dict[str,Any], op: Operation, start_us: float, die:int, plane:int) -> List[PhaseHook]:
    cfg_op = cfg["op_specs"][op.kind.name]
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
                hooks.append(PhaseHook(time_us=quantize(seg_start + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.START", die=die, plane=plane))
            elif rule["when"]=="STATE_MID":
                hooks.append(PhaseHook(time_us=quantize(seg_mid   + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.MID",   die=die, plane=plane))
            elif rule["when"]=="STATE_END":
                hooks.append(PhaseHook(time_us=quantize(seg_end   + rand_jitter(jitter)), label=f"{op.kind.name}.{seg.name}.END",   die=die, plane=plane))
        cur = seg_end
    return hooks

# ---------------------------- Address Manager ----------------------------

class AddressManager:
    def __init__(self, cfg: Dict[str, Any]):
        topo = cfg["topology"]
        self.cfg = cfg
        self.dies = topo["dies"]
        self.planes = topo["planes_per_die"]
        self.pages_per_block = topo["pages_per_block"]
        self.blocks_per_plane = topo["blocks_per_plane"]

        self.available: Dict[Tuple[int,int], float] = {(0,p): 0.0 for p in range(self.planes)}
        self.cursors: Dict[Tuple[int,int], List[int]] = {(0,p): [0,0,0] for p in range(self.planes)}  # [block, next_pgm_page, next_rd_page]
        self.programmed: Dict[Tuple[int,int], Set[Tuple[int,int]]] = {(0,p): set() for p in range(self.planes)}
        self.resv: Dict[Tuple[int,int], List[Tuple[float,float,Optional[int]]]] = {(0,p): [] for p in range(self.planes)}

        # soft-locks for seq read: (die,plane,block) -> {"expiry": float, "allowed_pages": set}
        self.seq_locks: Dict[Tuple[int,int,int], Dict[str,Any]] = {}
        # emergency GC per plane
        self.emgc: Dict[Tuple[int,int], bool] = {(0,p): False for p in range(self.planes)}

    def set_emergency_gc(self, die:int, plane:int, active: bool):
        self.emgc[(die,plane)] = bool(active)

    def set_seq_lock(self, die:int, plane:int, block:int, expiry_us: float, allowed_pages: Set[int]):
        key=(die,plane,block)
        self.seq_locks[key] = {"expiry": quantize(expiry_us), "allowed_pages": set(allowed_pages)}

    def update_seq_lock_pages(self, die:int, plane:int, block:int, allowed_pages: Set[int], extend_expiry: Optional[float]=None):
        key=(die,plane,block)
        lk = self.seq_locks.get(key)
        if not lk:
            return
        lk["allowed_pages"] = set(allowed_pages)
        if extend_expiry is not None:
            lk["expiry"] = quantize(max(lk["expiry"], extend_expiry))

    def clear_seq_lock(self, die:int, plane:int, block:int):
        self.seq_locks.pop((die,plane,block), None)

    # ---- observation / selection / checks ----

    def available_at(self, die:int, plane:int) -> float:
        return self.available[(die,plane)]

    def observe_states(self, die:int, plane:int, now_us: float):
        prog = len(self.programmed[(die,plane)])
        # naive buckets for demo
        pgmable_ratio  = "mid" if prog < 10 else "low"   # fewer programmed pages => "mid" (room to program)
        readable_ratio = "mid" if prog > 0 else "low"
        plane_busy_frac = "high" if self.available_at(die,plane) > now_us else "low"
        return ({"pgmable_ratio": pgmable_ratio, "readable_ratio": readable_ratio, "cls": "host"},
                {"plane_busy_frac": plane_busy_frac})

    def _plane_ring(self, start_plane:int, fanout:int, interleave:bool) -> List[int]:
        if fanout <= 1:
            return [start_plane]
        ring = []
        P = self.planes
        idx = start_plane
        for _ in range(fanout):
            ring.append(idx)
            idx = (idx + 1) % P
        return ring

    def select(self, kind: OpKind, die:int, plane:int, fanout:int=1, interleave:bool=True) -> List[Address]:
        planes = self._plane_ring(plane, fanout, interleave)
        targets: List[Address] = []
        for pl in planes:
            if kind == OpKind.READ:
                # If seq-lock exists, pick first allowed page if any; else fallback to smallest programmed
                # (to reduce precheck rejections)
                # find any lock on this plane
                locks_here = [k for k in self.seq_locks.keys() if k[0]==die and k[1]==pl]
                page = None; block = 0
                if locks_here:
                    d,p,b = locks_here[0]
                    lk = self.seq_locks[(d,p,b)]
                    if lk["allowed_pages"]:
                        page = sorted(lk["allowed_pages"])[0]
                        block = b
                if page is None:
                    prog = sorted(self.programmed[(die,pl)])
                    if prog:
                        block, page = prog[0]
                    else:
                        block, page = 0, 0
                targets.append(Address(die, pl, block=block, page=page))
            elif kind == OpKind.DOUT:
                raise RuntimeError("DOUT selection must be provided by obligation targets")
            elif kind == OpKind.PROGRAM:
                b, pgm_p, _ = self.cursors[(die,pl)]
                targets.append(Address(die, pl, block=b, page=pgm_p))
                # advance cursor
                pgm_p += 1
                if pgm_p >= self.pages_per_block:
                    pgm_p = 0
                    b = (b+1) % self.blocks_per_plane
                self.cursors[(die,pl)] = [b, pgm_p, self.cursors[(die,pl)][2]]
            elif kind == OpKind.ERASE:
                b = self.cursors[(die,pl)][0]
                targets.append(Address(die, pl, block=b, page=None))
            else:
                targets.append(Address(die, pl, block=0, page=0))
        return targets

    def precheck(self, kind: OpKind, targets: List[Address], start_hint: float) -> bool:
        start_hint = quantize(start_hint)
        end_hint = start_hint
        seen_planes = set()
        for t in targets:
            keyp = (t.die, t.plane)
            if keyp not in seen_planes:
                seen_planes.add(keyp)
                for (s,e,_) in self.resv[keyp]:
                    if not (end_hint <= s or e <= start_hint):
                        return False
            # soft-lock rules per block
            if t.block is not None:
                keyb = (t.die, t.plane, t.block)
                lk = self.seq_locks.get(keyb)
                if lk and start_hint < lk["expiry"]:
                    # READ: allow only pages within allowed set
                    if kind == OpKind.READ:
                        if t.page is None or t.page not in lk["allowed_pages"]:
                            return False
                    # PROGRAM/ERASE blocked unless emergency GC
                    if kind in (OpKind.PROGRAM, OpKind.ERASE):
                        if not self.emgc.get((t.die, t.plane), False):
                            return False
        return True

    def reserve(self, die:int, plane:int, start:float, end:float, block: Optional[int]=None):
        start = quantize(start); end = quantize(end)
        self.available[(die,plane)] = max(self.available[(die,plane)], end)
        self.resv[(die,plane)].append((start, end, block))

    def commit(self, op: Operation):
        t = op.targets[0]
        key = (t.die, t.plane)
        if op.kind == OpKind.PROGRAM and t.page is not None:
            self.programmed[key].add((t.block, t.page))
        elif op.kind == OpKind.ERASE:
            self.programmed[key] = {pp for pp in self.programmed[key] if pp[0] != t.block}

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
    is_seq: bool = False
    seq_id: Optional[int] = None

@dataclass
class SeqReadContext:
    seq_id: int
    die: int
    plane: int
    block: int
    start_page: int
    total_pages: int
    next_page: int
    window: int
    active_pages: Set[int] = field(default_factory=set)
    finished: bool = False

class ObligationManager:
    def __init__(self, cfg_list: List[Dict[str,Any]], addr: AddressManager, cfg: Dict[str,Any]):
        self.specs = cfg_list
        self.addr = addr
        self.cfg = cfg
        self.heap: List[_ObHeapItem] = []
        self._seq = 0
        self._obseq = 0
        # seq contexts
        self.seq_ctx_by_id: Dict[int, SeqReadContext] = {}
        self.seq_by_block: Dict[Tuple[int,int,int], int] = {}

    # ---- helpers ----
    def _push_ob(self, ob: Obligation):
        heapq.heappush(self.heap, _ObHeapItem(deadline_us=ob.deadline_us, seq=self._obseq, ob=ob))
        self._obseq += 1

    def _make_deadline(self, base: float, idx: int=0, spacing: float=0.0) -> float:
        return quantize(base + idx * spacing)

    # ---- public APIs ----
    def start_seq_read(self, now_us: float, die:int, plane:int, block:int, start_page:int, count:int, window: Optional[int]=None):
        W = window if window is not None else self.cfg["seq_read"]["window"]
        seq_id = self._seq; self._seq += 1
        ctx = SeqReadContext(seq_id=seq_id, die=die, plane=plane, block=block,
                             start_page=start_page, total_pages=count,
                             next_page=start_page, window=W)
        self.seq_ctx_by_id[seq_id] = ctx
        self.seq_by_block[(die,plane,block)] = seq_id

        spacing = self.cfg["seq_read"]["deadline_spacing_us"]
        # seed initial window
        to_seed = min(W, count)
        allowed: Set[int] = set()
        for i in range(to_seed):
            page = ctx.next_page
            ctx.active_pages.add(page); allowed.add(page)
            ctx.next_page += 1
            dl = self._make_deadline(now_us, i, spacing)
            ob = Obligation(require=OpKind.READ, targets=[Address(die,plane,block,page)],
                            deadline_us=dl, boost_factor=1.0, hard_slot=True,
                            is_seq=True, seq_id=seq_id)
            self._push_ob(ob)
        # set soft-lock with grace
        expiry = self._make_deadline(now_us, to_seed, spacing) + self.cfg["seq_read"]["softlock_grace_us"]
        self.addr.set_seq_lock(die, plane, block, expiry_us=expiry, allowed_pages=allowed)
        print(f"[{now_us:7.2f} us] SEQRD  start: die{die}/pl{plane}/b{block} p{start_page}..{start_page+count-1} W={W}")

    def _refill_after_read(self, now_us: float, seq_id: int, page_done: int):
        ctx = self.seq_ctx_by_id.get(seq_id)
        if not ctx or ctx.finished:
            return
        if page_done in ctx.active_pages:
            ctx.active_pages.remove(page_done)
        # refill if window allows and there are remaining pages
        if ctx.next_page < ctx.start_page + ctx.total_pages:
            # keep window size
            while len(ctx.active_pages) < ctx.window and ctx.next_page < ctx.start_page + ctx.total_pages:
                p = ctx.next_page
                ctx.active_pages.add(p)
                ctx.next_page += 1
                # deadline: from now or from last? Use now-based spacing for simplicity
                dl = self._make_deadline(now_us, 0, 0.0) + self.cfg["seq_read"]["deadline_spacing_us"]
                ob = Obligation(require=OpKind.READ, targets=[Address(ctx.die, ctx.plane, ctx.block, p)],
                                deadline_us=dl, boost_factor=1.0, hard_slot=True,
                                is_seq=True, seq_id=seq_id)
                self._push_ob(ob)
        # update soft-lock allowed pages & expiry
        allowed = set(ctx.active_pages)
        grace = self.cfg["seq_read"]["softlock_grace_us"]
        expiry = quantize(now_us + grace)
        self.addr.update_seq_lock_pages(ctx.die, ctx.plane, ctx.block, allowed_pages=allowed, extend_expiry=expiry)

        # finish condition (READ 측면): no more active and next_page reached end
        if not ctx.active_pages and ctx.next_page >= ctx.start_page + ctx.total_pages:
            ctx.finished = True
            # keep soft-lock a bit (until grace); let DOUTs finish by deadline+grace naturally
            print(f"[{now_us:7.2f} us] SEQRD  finished READ: die{ctx.die}/pl{ctx.plane}/b{ctx.block}")
            # Optionally clear when DOUT completes; for demo we retain until grace expiry

    def on_commit(self, op: Operation, now_us: float):
        # 1) generic issuer->require rules (READ -> DOUT)
        for spec in self.specs:
            if spec["issuer"] == op.kind.name:
                dt = sample_dist(spec["window_us"])
                ob = Obligation(
                    require = OpKind[spec["require"]],
                    targets = op.targets,
                    deadline_us = quantize(now_us + dt),
                    boost_factor = spec["priority_boost"]["boost_factor"],
                    hard_slot = spec["priority_boost"].get("hard_slot", False),
                )
                self._push_ob(ob)
                print(f"[{now_us:7.2f} us] OBLIG  created: {op.kind.name} -> {ob.require.name} by {ob.deadline_us:7.2f} us, target={_addr_str(ob.targets[0])}")

        # 2) seq-read bookkeeping on READ commit
        if op.kind == OpKind.READ:
            t = op.targets[0]
            seq_id = self.seq_by_block.get((t.die, t.plane, t.block))
            if seq_id is not None:
                self._refill_after_read(now_us, seq_id, t.page if t.page is not None else -1)

    def get_active_seq_info(self, die:int, plane:int) -> Optional[Tuple[int, Set[int]]]:
        # return (block, allowed_pages) if a seq-lock exists on this plane
        keys = [k for k in self.addr.seq_locks.keys() if k[0]==die and k[1]==plane]
        if not keys: return None
        d,p,b = keys[0]
        lk = self.addr.seq_locks[(d,p,b)]
        return b, set(lk["allowed_pages"])

    def pop_urgent(self, now_us: float, die:int, plane:int,
                   horizon_us: float, earliest_start: float) -> Optional[Obligation]:
        """Prefer seq READ obligations for this plane; else earliest feasible obligation."""
        if not self.heap: 
            return None
        kept: List[_ObHeapItem] = []
        chosen_seq: Optional[_ObHeapItem] = None
        chosen_any: Optional[_ObHeapItem] = None
        now_us = quantize(now_us)
        earliest_start = quantize(earliest_start)
        boost = self.cfg.get("seq_read", {}).get("boost_window_us", 2.5)

        while self.heap:
            item = heapq.heappop(self.heap)
            ob = item.ob
            tgt = ob.targets[0]
            same_plane = (tgt.die == die and tgt.plane == plane)
            in_horizon = ((ob.deadline_us - now_us) <= max(horizon_us, 0.0)) or ob.hard_slot
            feasible_time = (earliest_start <= ob.deadline_us)
            if not (same_plane and in_horizon and feasible_time):
                kept.append(item); continue
            # candidate
            if ob.is_seq and ob.require == OpKind.READ:
                # prefer seq READ if within boost window
                if (ob.deadline_us - now_us) <= boost or ob.hard_slot:
                    chosen_seq = item
                    break
                if not chosen_seq:
                    chosen_seq = item  # keep best seq-read encountered
            if not chosen_any:
                chosen_any = item
            else:
                # keep the earliest deadline among non-seq candidates
                if item.deadline_us < chosen_any.deadline_us:
                    kept.append(chosen_any); chosen_any = item
                else:
                    kept.append(item)
            # stop early if the peeked item is much later than now + horizon (heap is sorted)
            if (self.heap and self.heap[0].deadline_us - now_us) > horizon_us:
                break

        # restore heap
        for it in kept:
            heapq.heappush(self.heap, it)

        picked = chosen_seq or chosen_any
        if picked:
            return picked.ob
        return None

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

    def _roulette(self, dist: Dict[str, float], allow: set) -> Optional[str]:
        items = [(name, p) for name, p in dist.items() if name in allow and p > 0.0]
        if not items: 
            return None
        total = sum(p for _, p in items)
        r = random.random() * total
        acc = 0.0
        pick = items[-1][0]
        for name, p in items:
            acc += p
            if r <= acc:
                pick = name; break
        return pick

    def propose(self, now_us: float, hook: PhaseHook,
                global_state: Dict[str,str], local_state: Dict[str,str],
                earliest_start: float) -> Optional[Operation]:
        # 0) Obligation 우선 (plane/time 필터)
        ob = self.obl.pop_urgent(now_us, hook.die, hook.plane,
                                 horizon_us=10.0, earliest_start=earliest_start)
        if ob:
            if self.addr.precheck(ob.require, ob.targets, start_hint=earliest_start):
                cfg_op = self.cfg["op_specs"][ob.require.name]
                op = build_operation(ob.require, cfg_op, ob.targets)
                op.meta["source"]="obligation"
                op.meta["phase_key_used"]="(obligation)"
                if ob.is_seq: op.meta["seq_id"]=ob.seq_id
                return op

        # 1) Phase-conditional 분포 (단, 활성 seq가 있으면 policy READ는 회피)
        allow = set(self.cfg["op_specs"].keys()) - {"DOUT"}
        seq_info = self.obl.get_active_seq_info(hook.die, hook.plane)
        if seq_info:
            allow.discard("READ")  # READ는 의무로 처리; policy READ 회피
        dist, used_key = get_phase_dist(self.cfg, hook.label)
        fanout, interleave = get_phase_selection_override(self.cfg, hook.label, "<DEFAULT>")
        if dist:
            pick = self._roulette(dist, allow)
            if pick:
                fanout, interleave = get_phase_selection_override(self.cfg, hook.label, pick)
                kind = OpKind[pick]
                targets = self.addr.select(kind, hook.die, hook.plane, fanout=fanout, interleave=interleave)
                if targets and self.addr.precheck(kind, targets, start_hint=earliest_start):
                    op = build_operation(kind, self.cfg["op_specs"][pick], targets)
                    op.meta["source"]="policy.phase_conditional"
                    op.meta["phase_key_used"]=used_key
                    op.meta["fanout"]=fanout; op.meta["interleave"]=interleave
                    return op

        # 2) Backoff: score 방식
        cand = []
        for name in ["READ", "PROGRAM", "ERASE"]:
            if seq_info and name=="READ":
                continue  # 활성 seq가 있으면 READ는 의무에 맡긴다
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
        fanout, interleave = get_phase_selection_override(self.cfg, hook.label, pick)
        targets = self.addr.select(kind, hook.die, hook.plane, fanout=fanout, interleave=interleave)
        if not targets:
            return None
        if not self.addr.precheck(kind, targets, start_hint=earliest_start):
            return None
        op = build_operation(kind, self.cfg["op_specs"][pick], targets)
        op.meta["source"]="policy.score_backoff"
        op.meta["phase_key_used"]="(score_backoff)"
        op.meta["fanout"]=fanout; op.meta["interleave"]=interleave
        return op

# ---------------------------- Scheduler ----------------------------

class Scheduler:
    def __init__(self, cfg, addr_mgr: AddressManager, policy_engine: PolicyEngine, obl_mgr: ObligationManager):
        self.cfg=cfg; self.addr=addr_mgr; self.SPE=policy_engine; self.obl=obl_mgr
        self.now=0.0
        self.ev=[]  # (time, seq, type, payload)
        self._seq=0
        self.stat_propose_calls = 0
        self.stat_scheduled = 0
        self._push(0.0, "QUEUE_REFILL", None)
        for plane in range(self.addr.planes):
            self._push(0.0, "PHASE_HOOK", PhaseHook(0.0, "BOOT.START", 0, plane))

    def _push(self, t: float, typ: str, payload: Any):
        t = quantize(t)
        heapq.heappush(self.ev, (t, self._seq, typ, payload)); self._seq+=1

    def _unique_plane_keys(self, targets: List[Address]) -> List[Tuple[int,int,int]]:
        s=set()
        for a in targets:
            s.add((a.die, a.plane, a.block))
        return list(s)

    def _start_time_for_targets(self, targets: List[Address]) -> float:
        planes = {(a.die, a.plane) for a in targets}
        avail = [self.addr.available_at(d,p) for (d,p) in planes]
        return quantize(max([self.now] + avail))

    def _schedule_operation(self, op: Operation):
        start = self._start_time_for_targets(op.targets)
        dur = get_op_duration(op)
        end  = quantize(start + dur)
        for d,p,b in self._unique_plane_keys(op.targets):
            self.addr.reserve(d, p, start, end, block=b)
        self._push(start, "OP_START", op)
        self._push(end,   "OP_END",   op)
        for d,p,_ in self._unique_plane_keys(op.targets):
            hooks = make_phase_hooks(self.cfg, op, start, d, p)
            for h in hooks:
                self._push(h.time_us, "PHASE_HOOK", h)
        first = op.targets[0]
        print(f"[{self.now:7.2f} us] SCHED  {op.kind.name:7s} tgt={len(op.targets)} start={start:7.2f} end={end:7.2f} 1st={_addr_str(first)} src={op.meta.get('source')} key={op.meta.get('phase_key_used')} fanout={op.meta.get('fanout')} seq={op.meta.get('seq_id')}")
        self.stat_scheduled += 1

    def _update_emergency_gc(self, die:int, plane:int, global_state: Dict[str,str]):
        # crude rule: pgmable_ratio == "low" -> emergency GC on
        emgc = (global_state.get("pgmable_ratio") == "low")
        self.addr.set_emergency_gc(die, plane, emgc)

    def run_until(self, t_end: float):
        t_end = quantize(t_end)
        while self.ev and self.ev[0][0] <= t_end:
            self.now, _, typ, payload = heapq.heappop(self.ev)
            if typ=="QUEUE_REFILL":
                for plane in range(self.addr.planes):
                    self._push(self.now, "PHASE_HOOK", PhaseHook(self.now, "REFILL.NUDGE", 0, plane))
                nxt = quantize(self.now + self.cfg["policy"]["queue_refill_period_us"])
                self._push(nxt, "QUEUE_REFILL", None)

            elif typ=="PHASE_HOOK":
                hook: PhaseHook = payload
                earliest_start = self.addr.available_at(hook.die, hook.plane)
                global_state, local_state = self.addr.observe_states(hook.die, hook.plane, self.now)
                # Update emergency GC mode for this plane
                self._update_emergency_gc(hook.die, hook.plane, global_state)
                self.stat_propose_calls += 1
                op = self.SPE.propose(self.now, hook, global_state, local_state, earliest_start)
                if op:
                    self._schedule_operation(op)

            elif typ=="OP_START":
                op: Operation = payload
                first = op.targets[0]
                print(f"[{self.now:7.2f} us] START  {op.kind.name:7s} target={_addr_str(first)}")

            elif typ=="OP_END":
                op: Operation = payload
                first = op.targets[0]
                print(f"[{self.now:7.2f} us] END    {op.kind.name:7s} target={_addr_str(first)}")
                self.addr.commit(op)
                self.obl.on_commit(op, self.now)

        print(f"\n=== Stats ===")
        print(f"propose calls : {self.stat_propose_calls}")
        print(f"scheduled ops : {self.stat_scheduled}")
        if self.stat_propose_calls:
            rate = 100.0 * self.stat_scheduled / self.stat_propose_calls
            print(f"accept ratio  : {rate:.1f}%")

# ---------------------------- Main ----------------------------

def main():
    random.seed(CFG["rng_seed"])
    addr = AddressManager(CFG)
    obl  = ObligationManager(CFG["obligations"], addr, CFG)
    spe  = PolicyEngine(CFG, addr, obl)
    sch  = Scheduler(CFG, addr, spe, obl)
    run_until = CFG["policy"]["run_until_us"]

    # --- Demo: host sequential READ request (20 pages from block 0, page 0) on die0/plane0 ---
    obl.start_seq_read(now_us=0.0, die=0, plane=0, block=0, start_page=0, count=20, window=CFG["seq_read"]["window"])

    print("=== NAND Sequence Generator Demo (P2: seq-read window + softlock + emGC) ===")
    sch.run_until(run_until)
    print("=== Done ===")

if __name__ == "__main__":
    main()

```

포인트 정리
- 순차 READ 윈도: 항상 W개 의무만 outstanding. READ 커밋 시 DOUT 의무 발행 + 다음 페이지 READ 보충.
- 소프트 락: 해당 블록에 대해 허용 페이지만 READ 가능, ERASE/PROGRAM은 응급 GC 모드가 아니면 거절.
- 우선순위: pop_urgent가 seq READ를 먼저 집어오고, PolicyEngine은 활성 seq가 있으면 policy READ를 회피 → host latency 보장 + policy 충돌 감소.
- 응급 GC: pgmable_ratio == "low" 일 때 plane별 활성. (간단 규칙; CFG 임계치/정밀화는 다음 단계에서 조정 가능)

필요하면:
- 캐시 READ → DOUT 상대 순서(READ(p+1) 앞선 뒤 DOUT(p))까지 확장,
- 의존성 룰 강화(PGM↔READ/ERASE↔READ 금지의 상세 스코프)도 즉시 이어서 붙일 수 있어.
