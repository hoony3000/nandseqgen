# nandseqgen Project PRD

---
## 개발 도구
### Python 3.10
#### numpy

---
## 목표
### Flash Memory NAND 에 입력할 randomized command sequence 생성한다.
### clock time 기반으로 NAND 의 state 에 따라 randomized command sequence 가 생성되는 dynamic system 을 구현한다.

---
## 구성 요소
### -. class: **Clock**
#### 전체 System 에 적용되는 Clock.
#### systemtime 의 시간을 수정하거나 초기화 가능.
#### HostReqGen, NANDScheduler 에 적용.
#### systemtime 속성을 지님.
#### int: **systemtime**

### -. class: **AddressManager**
#### NAND Address 별로 Erase, PGM, Read 가능한 상태를 업데이트 한다.
#### num_address, pagesize, readoffset, addrstates, addrErasable, addrPGMable, addrReadable, isErasable, isPGMable, isReadable 속성을 지님.
#### int: **pagesize**
##### 한 block 안에서 page 의 갯수
#### **addrstates**
###### -4: not usable
###### -3: ready to use
###### -2: PGM closed
###### -1: erased
###### 0 to pagesize-1 : PGM 된 page 수
##### 배열의 형태
##### 원소의 값에 따라 다음과 같은 상태를 나타낸다.
#### List of int: **addrErasable**
##### 현재 Erase 동작이 가능한 address 의 집합
#### List of (int, int): **addrPGMable**
##### 현재 PGM 동작이 가능한 address 의 집합
#### List of (int, int): **addrReadable**
##### 현재 Read 동작이 가능한 address 의 집합
#### boolean: **isErasable**
#### 현재 addrErasable 의 원소의 갯수가 0 이상이면 True
#### boolean: **isPGMable**
#### 현재 addrPGMable 의 원소의 갯수가 0 이상이면 True
#### boolean: **isReadable**
#### 현재 addrReadable 의 원소의 갯수가 0 이상이면 True

### -. class: **StateSeq**
#### StateManager 로 statetable 에 등록할 data
#### states, times, id 속성을 지님.
#### List of str: **states**
##### 문자열로 된 state 의 배열
#### List of int: **times**
##### 각 state 의 execution time 의 배열
#### int: **id**
##### 최초 StateSeq 가 생성될 때, 부여되는 값.
##### id = hash of (states, times)
##### 생성 시 동일한 states 와 times 를 가진 StateSeq 의 instance 가 이미 생성돼 있다면, instance 를 재생성 하지 말고 기존 instance 를 반환할 수 있게 한다.
#### StateSeq 끼리 + 연산 정의.
##### 최종 결과 C 는 StateSeq 형태
##### C.states = concat(A.states, B.states)
##### C.times = concat(A.times, B.times)
##### C.id = hash of (C.states, C.times)

### -. class: **StateTable**
#### StateSeq 를 입력 받아 statetable 에 추가 및 변경한다.
#### clock, statetable 속성을 지님.
#### Clock: **clock**
##### Clock 의 instance.
#### StateSeq: **statetable**
##### clock 의 현재 시각 이후의 StateSeq 를 등록한다.
##### 특정 시각 t 에서의 state 값을 반환한다.

### -. class: **Operation**
#### statetable 과 optable 에 등록될 NAND 동작의 단위.
#### name, stateseq, id 속성을 지님.
#### str: **name**
##### 동작의 이름.
#### StateSeq: *stateseq**
##### StateSeq 의 instance.
#### int: **id**
##### 최초 Operation 이 생성될 때, 부여되는 값.
##### id = hash of (name, stateseq)
##### 생성 시 동일한 name 과 stateseq 를 가진 Operation 의 instance 가 이 이미 생성돼 있다면, instance 를 재생성 하지 말고 기존 instance 의 반환할 수 있게 한다.

### -. class: **OperSeq**
#### Operation 의 배열.

### -. class: **OperManager**
#### Operation, time 을 입력 받아 optable 에 추가 및 변경한다.
#### optable 속성을 지님.
#### List of (int, str): **optable**
##### Operation 을 time 시점에 수행하도록 등록

### -. class: **HostReq**
#### Host level 에서 NAND 에 특정 Command 를 수행하도록 하는 명령 단위.
#### id, name 속성을 지님.
#### HostReq List 는 별도의 host_req.yaml 파일에 정의.

### -. class: **HostReqGen**
#### NAND 에 입력할 OperSeq 를 생성하는 HostReq 를 생성.
#### 생성될 때마다 id, time, args 가 생성된다.
#### 생성된 Request 가 NAND 의 Erase 와 PGM 동작이라면 args 중 일부는 address 의 배열이 되고, 그 address 의 상태는 addrstates 에 업데이트 된다.
#### id, args 는 그 형태와 값이 addrstates 의 값과 systemtime 에서의 NAND state 에 dependent 하게 생성된다. 각 id 별 args 생성 규칙은 별도로 정의한다.
#### 생성이 완료된 HostReq 는 HostReqInterpreter 의 입력으로 사용된다.

### -. function: **HostReqInterpreter**
#### HostReq 로 입력을 받고 해석해서, NAND 에 입력할 OperSeq 를 생성.
#### HostReq 를 OperSeq 로 변환하는 규칙은 req_to_operseq.yaml 파일에서 읽어온다.

### -. class: **NANDScheduler**
#### OperSeq 를 입력으로 받아 어떤 Operation 을 어느 시점에 statetable 과 optable 에 추가할 지 우선순위 관리.
#### systemtime 에서의 state 의 값에 따라 즉시 추가 가능한 것과 특정 시점에 수행하도록 예약할 것이 정해진다. 그 규칙은 별도로 정의한다.
#### clock 속성을 지님.
#### **clock**
##### Clock 의 instance

### -. dict: **StateOperWeight**
#### State 와 Operation 의 조합에 해당하는 0-1 사이의 확률 값을 정의.
#### config.yaml 파일에 정의

### -. dict: **TimeSpec**
#### state 마다 지속되는 시간을 정의.
#### config.yaml 파일에 정의

### -. dict: **ExecTime**
#### Operation 에서 command execution time 을 정의.
#### config.yaml 파일에 정의

---
## Test Flow 정의
### 1. 초기화
#### -. StateOperWeightDict 생성 : TimeSpec.yaml 파일
#### -. timeSpecDict 생성 : TimeSpec.yaml 파일
#### -. hostReqDict 생성 : host_req.yaml 파일로부터 HostReq list dictionary 생성. 
#### -. reqToOperSeqDict 생성 : host_req.yaml 파일로부터 HostReq to OperSeq list dictionary 생성.
#### -. 모든 종류의 StateSeq instance 생성 : 별도 사용자 정의
#### -. 모든 종류의 Operation instance 생성 : 별도 사용자 정의
#### -. 모든 종류의 Operseq 생성 : 별도 사용자 정의
#### -. StateManager instance 생성
#### -. AddressManager instance 생성 : addrstates 의 값은 -4 로 초기화
#### -. addrstates 의 값을 최초로 업데이트 하는 sequence 실행 : 별도로 정의
#### -. OperManager instance 생성
#### -. NANDScheduler instance 생성
### 2. t 랜덤 샘플링 후 t 만큼 systemtime 변경.
### 3. StateManger 로 현재 시각의 state 반환하여 currstate 에 저장
### 4. AddressManager 로 isErasable, isPGMable, isReadable 을 반환하여 isOper 배열에 모두 저장.
### 5. currstate, isOper 를 입력 받아 HostReqGen 로 HostReq 를 생성
### 6. HostReq 를 입력 받아 HostReqInterpreter 로 OperSeq 생성
### 7. OperSeq 를 입력 받아 NANDScheduler 로 statetable 가 optable 을 업데이트
### 8. 2번에서 7번 과정을 N번 반복.
### 9. statetable 과 optable 의 내용을 log 파일로 저장



