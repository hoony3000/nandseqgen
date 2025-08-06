from addrman import *
import os
import yaml
import numpy as np
from dataclasses import dataclass, field
import weakref
import threading
import hashlib
import itertools
from typing import Union, List, Tuple, TypeVar, Generic, Callable, Any, Set
import bisect

NS = 1
US = 10**3
MS = 10**6
S = 10**9
INF = 20 * 10**9


# 중복 instance 생성 방지를 위한 parent class 정의


class DeduplicateBase:
    """
    multi-thread 환경(필요하다면)에서 instance 중복 생성 방지를 위한 id, lock, counter
    """

    _global_lock = threading.Lock()
    _global_instances = weakref.WeakValueDictionary()
    _class_counters = {}
    _class_instances_by_id = {}

    @classmethod
    def _get_class_counter(cls):
        """
        instance 생성 시 id, counter 값 부여
        """
        if cls not in cls._class_counters:
            cls._class_counters[cls] = itertools.count()
            # python 내부 메모리 관리를 위한 garbage collection 을 위한 구문. chatGPT 제안해서 씀
            cls._class_instances_by_id[cls] = {}
        return cls._class_counters[cls]

    @classmethod
    def create(cls, *args, force_new=False, **kwargs):
        """
        instance 생성자
        """
        key = cls._make_key(*args, **kwargs)
        if not force_new:
            with cls._global_lock:
                instance = cls._global_instances.get((cls, key))
                if instance:
                    return instance

        obj = cls(*args, **kwargs)
        obj.id = next(cls._get_class_counter())

        with cls._global_lock:
            cls._class_instances_by_id[cls][obj.id] = obj
            if not force_new:
                cls._global_instances[(cls, key)] = obj

        return obj

    @classmethod
    def get_by_id(cls, id):
        """
        id 로 instance 반환
        """
        return cls._class_instances_by_id[cls].get(id, None)

    @classmethod
    def len_class(cls):
        """
        동일 class 내 instance 갯수 반환
        """
        return len(cls._class_instances_by_id[cls])

    @classmethod
    def _make_key(cls, *args, **kwargs):
        """
        child class 선언 시 id 생성 규칙 정의 강제하기 위한 구문
        """
        raise NotImplementedError()


@dataclass
class NamedInstanceBase(DeduplicateBase):
    """
    name 으로 instance 를 반환 가능한 base class
    name: instance 이름
    id : instance id
    _instance_by_name : name 으로 instance 반환하기 위한 dict
    """

    name: str
    id: int = field(init=False, default=None)
    _instance_by_name = {}

    @classmethod
    def get_instance_by_name(cls, name: str):
        return cls._instance_by_name.get(name, None)

    @classmethod
    def get_id_by_name(cls, name: str):
        return cls.get_instance_by_name(name).id

    @classmethod
    def samples(
        cls, size: int = 1, replace: bool = True, weights: List | np.ndarray = None
    ):
        size_all = cls.len_class()
        _weights = weights
        _size = size
        if weights is None:
            if size > size_all:
                _size = size_all
        else:
            if len(weights) == size_all:
                _weights = arr_to_nparr(weights)
            else:
                raise ValueError(
                    f"len(weights) does not match the size of all instances: {len(weights)} != {size_all}"
                )
        ids = np.random.choice(cls.len_class(), size=_size, replace=replace, p=_weights)
        return [cls.get_by_id(id) for id in ids]
    
    def __post_init__(self):
        """
        _instance_by_name update
        """
        self.__class__.instance_by_name[self.name] = self

    def __repr__(self):
        return f"class:{self.__class__.__name__}, name:{self.name}, id:{self.id}"



@dataclass
class StateSeq(DeduplicateBase):
    """
    StateSeq class 정의
    times : time sequence
    states : state sequence
    id : instance 생성 시 고유 값 부여. 0부터 시작
    length : times 또는 states 의 길이
    """

    times: Union[int, float, List[int], List[float], np.ndarray]
    states: Union[str, List[str], np.ndarray]
    id: int = field(init=False, default=None)

    @classmethod
    def _make_key(
        cls,
        times: Union[int, float, List[int], List[float], np.ndarray],
        states: Union[str, List[str], np.ndarray],
    ):
        """
        instance 의 unique 한 id 생성을 위한 hash key 생성 함수
        """
        time_array = np.array(times)
        state_array = np.array(states)
        return hashlib.sha256(time_array.tobytes() + state_array.tobytes()).hexdigest()

    def __post_init__(self):
        """
        times, states 의 type, 두 배열 길이 일치 여부 확인
        """
        # times 의 type check
        if isinstance(self.times, (int, float)):
            self.times = np.array([self.times], dtype=float)
        elif isinstance(self.times, np.ndarray) and self.times.dtype.kind in {"i", "u"}:
            self.times = np.array(self.times, dtype=float)
        elif isinstance(self.times, np.ndarray) and self.times.dtype.kind == "f":
            self.times = np.array(self.times, dtype=float)
        elif isinstance(self.times, (list, tuple)):
            if all(isinstance(v, (float, int)) for v in self.times):
                self.times = np.array(self.times, dtype=float)
            else:
                raise ValueError("List elements must be str")
        else:
            raise ValueError(f"Unsupported input type (times): {type(self.times)}")

        # states 의 type check
        if isinstance(self.states, str):
            self.states = np.array([self.states], dtype=str)
        elif isinstance(self.states, np.ndarray) and self.states.dtype.kind in {
            "U",
            "S",
        }:
            self.states = np.array(self.states, dtype=str)
        elif isinstance(self.states, list):
            if all(isinstance(v, str) for v in self.states):
                self.states = np.array(self.states, dtype=str)
            else:
                raise ValueError("List elements must be str")
        else:
            raise ValueError(f"Unspported input type (states): {type(self.states)}")

        # times 와 states 의 차원 비교 및 length
        if len(self.times) == len(self.states):
            self.legnth = len(self.times)
        else:
            raise ValueError("len(times) != len(states)")

    def __add__(self, other: "StateSeq") -> "StateSeq":
        """
        연산자 오버로딩 : 덧셈
        """
        if not isinstance(other, StateSeq):
            raise ValueError("StateSeq 인스턴스만 추가 가능")
        # stateseq_A + stateseq_B : state_a.times 의 마지막 원소만큼 state_b.times 에 offset 값을 더해줌
        shifted_times = other.times + self.times[-1]
        return self.create(
            np.concat((self.times, shifted_times)),
            np.concat((self.states, other.states)),
        )

    def __mul__(self, val: int) -> "StateSeq":
        """
        연산자 오버로딩 : 곱셈
        """
        # StateSeq 와 1 이상인 자연수 끼리의 곱셈에서만 정의 된다.
        if not isinstance(val, int) or val <= 0:
            raise ValueError(f"val 값은 1이상인 자연수여야 합니다")
        result = self
        multiplier = self
        for i in range(val - 1):
            result += multiplier
        return result

    def __hash__(self):
        """
        연산자 오버로딩 : array[stateseq] 형태의 instance 가 배열의 index 형태로 참조될 때의 값을 정의
        """
        return self.id

    def __eq__(self, other):
        """
        연산자 오버로딩 : == 연산자
        """
        return isinstance(other, StateSeq) and self.id == other.id

    def __repr__(self):
        """
        연산자 오버로딩 : print(stateseq) 시 출력 값 정의
        """
        return f"StateSeq id={self.id}, times_len={len(self.times)}, states_len={len(self.states)}"

    def shift_time(self, time: float) -> "StateSeq":
        """
        time 의 원소값에 offset 더함
        """
        times = self.times + time
        return self.create(times, self.states)

    def add_prefix(self, string: str) -> "StateSeq":
        """
        states 의 원소값에 prefix 를 붙임
        """
        states = [string + s for s in self.states]
        return self.create(self.times, states)

    def add_suffix(self, string: str) -> "StateSeq":
        """
        states 의 원소값에 suffix 를 붙임
        """
        states = [s + string for s in self.states]
        return self.create(self.times, states)

    def remove_prefix(self, string: str) -> "StateSeq":
        """
        states 의 원소값에서 prefix 를 제거
        """
        states = [s.removeprefix(string) for s in self.states]
        return self.create(self.times, states)

    def remove_suffix(self, string: str) -> "StateSeq":
        """
        states 의 원소값에서 suffix 를 제거
        """
        states = [s.removesuffix(string) for s in self.states]
        return self.create(self.times, states)

    def slice_tail(self, idx: int) -> "StateSeq":
        """
        times, states 에서 tail 을 slice
        """
        times = self.times[idx:]
        states = self.states[idx:]
        return self.create(times, states)

    def slice_head(self, idx: int) -> "StateSeq":
        """
        times, states 에서 head 를 slice
        """
        times = self.times[:idx]
        states = self.states[:idx]
        return self.create(times, states)

    def slice_one(self, idx: int) -> "StateSeq":
        """
        times, states 에서 원소 하나만 slice
        """
        times = self.times[idx]
        states = self.states[idx]
        return self.create(times, states)

    def get_idx_by_time(self, time: float) -> int:
        """
        times 의 원소와 time 을 비교해서 time 에 근접한 index 를 반환(bisect 참조)
        """
        return bisect.bisect_left(self.times, time)

    def get_state_by_time(self, time: float) -> str:
        """
        times 의 원소와 time 을 비교해서 time 에 근접한 index 에서의 state 값을 반환
        """
        idx = self.get_idx_by_time(time)
        return str(self.states[idx])

    def cut_time(self, time: float) -> "StateSeq":
        """
        times 의 원소 중 time 보다 큰 원소를 제거하고 마지막 원소는 time 과 동일한 값으로 치환
        """
        idx = self.get_idx_by_time(time)

        if self.legnth == idx + 1:
            tmp_seq = self.slice_tail(0)
        else:
            tmp_seq = self.slice_head(idx + 1)

        times = tmp_seq.times.copy()
        times[-1] = time
        states = tmp_seq.states.copy()
        return self.create(times, states)

    def get_times(self) -> np.ndarray[float]:
        """
        times 반환
        """
        return self.times

    def get_time_by_idx(self, idx) -> float:
        """
        times 의 마지막 원소 반환
        """
        return self.times[idx]

    def get_lasttime(self) -> float:
        """
        times 의 마지막 원소 반환
        """
        return self.times[-1]

    def get_firsttime(self) -> float:
        """
        times 의 첫 번째 원소 반환
        """
        return self.times[0]

    def get_first(self) -> "StateSeq":
        """
        times 와 states 의 첫 번째 원소를 slice
        """
        return self.slice_one(0)

    def get_last(self) -> "StateSeq":
        """
        times 와 states 의 마지막 원소를 slice
        """
        return self.slice_one(-1)

    def get_states(self) -> List[str]:
        """
        states 반환
        """
        return self.states

    def get_pairs(self) -> Tuple[Tuple[float, str]]:
        """
        times 와 states 의 원소 pair 를 원소르ㅗ 하는 tuple 반환
        """
        return tuple(zip(self.times.tolist(), self.states.tolist()))

    def len(self) -> int:
        """
        times, 또는 states 의 length 반환
        """
        return self.length

    def squeeze_targets(self, targets: Set[str]):
        """
        연속된 중복 상태를 제거하기 위한 함수
        """
        if len(self.states) == 0:
            return self

        if not isinstance(targets, set):
            raise ValueError(f"targets is not a type of set : {type(targets)}")
        elif len(targets) == 0:
            raise ValueError(f"targets is empty")

        extended_states = np.append(self.states[1:], None)
        is_last = self.states != extended_states
        is_target = np.isin(self.states, list(targets))
        keep_mask = ~is_target | is_last

        times = self.times[keep_mask]
        states = self.states[keep_mask]

        return self.create(times, states)

    def toList(self) -> Tuple[List[float], List[str]]:
        """
        times, states 의 tuple 형태로 반환
        """
        return self.times[:], self.states[:]


def Idle(time: float) -> StateSeq:
    """
    idle 을 state 로 하는 stateseq 생성 함수
    """
    return StateSeq.create(np.array([time], dtype=float), ["idle"])


def Nop(time: float) -> StateSeq:
    """
    Nop 를 state 로 하는 stateseq 생성 함수
    """
    return StateSeq.create(np.array([time], dtype=float), ["nop"])


def End(state: str) -> StateSeq:
    """
    입력받은 str 를 state 로 하는 stateseq 생성 함수
    """
    return StateSeq.create(np.array([INF], dtype=float), [state])


@dataclass
class Operation(NamedInstanceBase):
    """
    Operation class 정의
    name : operation 이름
    seq : StateSeq instance
    applyto : die-level 또는 plane-level
    """

    name: str
    seq: StateSeq
    applyto: str
    id: int = field(init=False, default=None)

    @classmethod
    def _make_key(cls, name: str, seq: StateSeq, applyto: str):
        """
        instance 의 unique 한 id 생성을 위한 hash key 생성 함수
        """
        return hashlib.sha256(
            np.array(name + str(seq.id) + applyto).tobytes()
        ).hexdigest()

    def __repr__(self):
        return super().__repr__()

    def get_seq(self) -> StateSeq:
        """
        stateseq 반환
        """
        return self.seq

    def get_applyto(self) -> str:
        """
        applyto 반환
        """
        return self.applyto


class OperSeq:
    """
    Operation Sequence class 정의
    """

    def __init__(self, operations: List[Operation]):
        """
        생성자 정의
        """
        self.operations = operations


class OperManager:
    """
    OperManager class 정의
    """

    def __init__(self):
        """
        생성자 정의
        구현 필요
        """
        pass


@dataclass
class HostReq(NamedInstanceBase):
    """
    HostReq class 정의
    name : Request name
    id : Request 고유값
    """

    name: str
    id: int = field(init=False, default=None)

    @classmethod
    def _make_key(cls, name: str):
        """
        instance 의 unique 한 id 생성을 위한 hash key 생성 함수
        """
        return hashlib.sha256(np.array([name], dtype=str).tobytes()).hexdigest()

    def __repr__(self):
        return super().__repr__()


class HostReqGen:
    """
    HostReqGen class 정의
    """

    def __init__(self):
        """
        생성자 정의
        구현 필요
        """
        pass


class HostReqInterpreter:
    """
    HostReqInterpreter class 정의
    """

    def __init__(self):
        """
        생성자 정의
        구현 필요
        """
        pass


class Clock:
    def __init__(self, time):
        """
        생성자 정의
        """
        self.time = time

    def init(self, time):
        """
        set time
        """
        self.time = time

    def get_time(self) -> float:
        """
        time 반환
        """
        return self.time

    def forward(self, duration):
        """
        현재 시간에 offset 값 더함
        """
        self.time += duration


# Generic type 정의를 위한 T variable 선언
T = TypeVar("T")


class Matrix2D(Generic[T]):
    """
    class instance 의 2차원 배열 indexing 을 tuple 형태로 간단하게 표현하기 위해서, 가독성을 위해서 정의한 helper class
    ex) statetable[0][1] -> statetable[0, 1]
    목적은 numpy 2차원 배열을 순회할 때 nested for 문을 사용하지 않고 간단하게 1차원 for 문을 사용할 수가 있는데,
    이걸 numpy 배열 뿐만 아니라 instance 의 2차원 배열로 확장하기 위해서 trick 을 썼음.
    코드 가독성과 type hint 를 사용한 자동 완성 기능을 위해서 특별히 고안됨.
    """

    def __init__(self, data: List[List[T]]):
        """
        input 으로 class instance 의 2차원 배열을 받음
        """
        self._data = data
        self.rows = len(data)
        self.cols = len(data[0])

    def __getitem__(self, idx: Tuple[int, int]) -> T:
        """
        연산자 정의 : ex) Matrix2D[i, j]
        """
        i, j = idx
        return self._data[i][j]

    def __setitem__(self, idx: Tuple[int, int], value: T):
        """
        연산자 정의 : ex) Matrix2D[i, j] = value
        """
        i, j = idx
        self._data[i][j] = value

    def as_list(self) -> List[List[T]]:
        """
        list 형태로 반환
        """
        return self._data

    def argmin(self, key: Callable[[T], Any]) -> Tuple[int, int]:
        """
        class instance 의 원소값 중 최소 값을 찾기 위한 함수 정의
        """
        min_val = None
        min_index = (-1, -1)
        for i in range(self.rows):
            for j in range(self.cols):
                val = key(self[i, j])
                if min_val is None or val < min_val:
                    min_val = val
                    min_index = (i, j)
        return min_index

    def argmax(self, key: Callable[[T], Any]) -> Tuple[int, int]:
        """
        class instance 의 우ㅓㄴ소값 중 최대 값을 찾기 위한 함수 정의
        """
        max_val = None
        max_index = (-1, -1)
        for i in range(self.rows):
            for j in range(self.cols):
                val = key(self[i, j])
                if max_val is None or val > max_val:
                    max_val = val
                    max_index = (i, j)
        return max_index


class StateTable:
    """
    StateTable class 정의
    clock : Clock instance
    seq : StateSeq instance
    """

    clock: Clock
    seq: StateSeq

    def __init__(self, clock: Clock):
        """
        instsance 생성자 정의
        """
        self.clock = clock
        self.seq = Idle(self.clock.get_time())

    def set(self, stateseq: StateSeq):
        """
        seq 값 할당
        """
        self.seq = stateseq

    def add(self, stateseq: StateSeq):
        """
        기존 seq 값 뒤에 new seq 추가
        """
        self.seq += stateseq

    def setnow(self, stateseq: StateSeq):
        """
        현재 시각 기준으로 그 뒤에 seq 는 삭제하고 new seq 로 대체
        """
        tmp_seq = self.seq.cut_time(self.get_time())
        self.seq = tmp_seq + stateseq

    def stat(self):
        """
        현재 seq 값 출력
        """
        print(self.seq.get_pairs())

    def update(self):
        """
        현재 시각 기준으로 진행되고 있는 seq. 제외한 과거의 seq 삭제
        """
        idx = bisect.bisect_right(self.seq.get_times(), self.get_time())
        self.seq = self.seq.slice_tail(idx)

    def gettimeleft(self) -> Tuple[Tuple[float, str]]:
        """
        현재 시각 기준으로 미래의 state 의 time 이 얼마나 남았는지 반환
        """
        tmp_seq = self.seq.shift_time(self.get_time() * -1)
        return tuple(zip(tmp_seq.get_times(), tmp_seq.get_states()))

    def get_lasttime(self, time: float) -> float:
        """
        time 시간에서의 state 의 값 반환
        """
        if time < self.get_time():
            raise ValueError("time < current time")
        return self.seq.get_state_by_time(time)

    def get_time(self) -> float:
        """
        현재 시각 반환
        """
        return self.clock.get_time()

    def get_state(self) -> str:
        """
        현재 시각에서의 state 의 값 반환
        """
        return self.seq.get_state_by_time(self.get_time())

    def get_stat(self) -> Tuple[str, float]:
        """
        현재 시각과 state 를 반환
        """
        time = self.get_time()
        return time, self.expect(time)

    def squeeze(self) -> StateSeq:
        """
        2번 이상 연속적으로 반복되는 state 원소들을 합침
        """
        self.seq = self.seq.squeeze_targets({"idle", "nop"})


class StateMapper:
    def __init__(self, path: str = "state_mapping.yaml"):
        self.rules = {}
        self.path = path
        self.load(path)

    def load(self, path: str):
        """
        yaml 파일에서 prefix rule 불러오기
        """
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.rules = yaml.safe_load(f) or {}
            print(f"[INFO] Loaded state mapping from {path}")
        else:
            print(f"[INFO] No state mapping file at {path}, starting fresh")

    def save(self, path: str = None):
        """
        prefix rule 을 yaml 파일로 저장
        """
        if path is None:
            path = self.path
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.rules, f, default_flow_style=False, allow_unicode=True)
        print(f"[INFO] State mapping save to {path}")

    def register_prefix(self, base_state: str, prefix: str):
        self.rules[base_state] = prefix

    def apply_prefix(self, op: Operation):
        """
        prefix rule 적용
        """
        seq = op.get_seq()
        # 구현 필요

    def ensure_prefix(self, op: Operation):
        """
        등록되지 않은 상태에 대해 사용자 입력을 통해 prefix rule 동적 등록
        """
        states = op.get_seq()
        for state in states:
            if state in self.rules:
                prefix = input(
                    f"[INPUT] Enter prefix for state '{state}' (leave empty to skip) "
                )
                self.rules[state] = prefix
        self.save()


class NANDScheduler:
    """
    statetable 을 관리하기 위한 주체 class 정의
    Scheduler 를 통해서만 statetable 에 Operation 추가 및 삭제 가능
    clock : Clock instance
    num_die : die 갯수
    num_plane : plane 갯수
    targets : die-level 또는 plane-level 에서 seq 추가를 위해 만든 index 배열
    """

    def __init__(self, num_dies: int, num_planes: int, num_blocks: int, pagesize: int):
        """
        생성자 정의
        """
        self.clock: Clock = Clock(0)
        self.num_dies: int = num_dies
        self.num_planes: int = num_planes
        self.num_blocks: int = num_blocks
        self.statetable = Matrix2D(
            [
                [StateTable(self.clock) for _ in range(self.num_planes)]
                for _ in range(self.num_die)
            ]
        )
        self.targets = np.zeros((num_dies, num_planes))
        self.indice = tuple(np.ndindex(self.targets.shape))
        self.mapper = StateMapper()
        self.addman = [
            AddressMananer(num_planes, num_blocks, pagesize, GOOD)
            for _ in range(self.num_dies)
        ]
        self.cur_states = np.full((num_dies, num_planes), "idle", dtype=str)

    def get_time(self):
        """
        현재 시각 반환
        """
        return self.clock.get_time()

    def set(self, sel_die: int, sel_plane: int, op: Operation):
        """
        operation 할당
        """
        applyto = op.get_applyto()
        seq = op.get_seq()
        self._set_targets(sel_die, sel_plane, applyt=applyto)
        for idx in self.indice:
            if self.targets[idx] == 1:
                self.statetable[idx].set(seq)
            else:
                self.statetable[idx].set(Nop(seq.get_firsttime()))

    def add(self, sel_die: int, sel_plane: int, op: Operation):
        """
        Operation 추가
        """
        applyto = op.get_applyto()
        seq = op.get_seq()
        self._set_targets(sel_die, sel_plane, applyto=applyto)
        for idx in self.indice:
            if self.targets[idx] == 1:
                self.statetable[idx].add(seq)

        idx_max = self.statetable.argmax(lambda x: x.get_lasttime())
        max_val = self.statetable[idx_max].get_lasttime()

        for idx in self.indice:
            if idx != idx_max:
                self.statetable[idx].add(
                    Nop(max_val - self.statetable[idx].getlasttime())
                )

    def setnow(self, sel_die: int, sel_plane: int, op: Operation):
        """
        현재 시각 이후의 예약된 stateseq 를 삭제하고 new stateseq 추가
        """
        applyto = op.get_applyto()
        seq = op.get_seq()
        self._set_targets(sel_die, sel_plane, applyto=applyto)
        for idx in self.indice:
            if self.targets[idx] == 1:
                self.statetable[idx].setnow(seq)
            else:
                self.statetable[idx].setnow(seq.get_first())

    def stat(self, sel_die: int, sel_plane: int):
        """
        statetable 의 등록된 stateseq 를 모두 출력
        """
        self._set_targets(sel_die, sel_plane)
        for idx in self.indice:
            if self.targets[idx] == 1:
                print(f"die: {idx[0]}, plane: {idx[1]}")
                self.statetable[idx].stat()

    def get_aheadtime(self, time: float) -> float:
        """
        (현재 시각 + time) 반환
        """
        return self.get_time() + time

    def expect(self, sel_die: int, sel_plane: int, time: float) -> str:
        """
        (현재 시각 + time) 에서의 state 반환
        """
        _time = self.get_aheadtime(time)
        return self.statetable[sel_die, sel_plane].expect(_time)

    def expect_all(self, time: float) -> List[List[str]]:
        """
        expect 를 모든 statetable 에 대해서 수행한 값을 반환
        """
        _time = self.get_aheadtime(time)
        self._set_targets(-1, -1)
        return [
            [
                self.statetable[die, plane].expect(_time)
                for plane in range(self.num_plane)
            ]
            for die in range(self.num_die)
        ]

    def _set_targets(self, sel_die: int, sel_plane: int, applyto="plane"):
        """
        어느 level 에서 operation 을 수행할 지 self.targets 에 기록
        """
        self.targets.fill(0)
        if applyto == "die":
            sel_plane = -1

        if sel_die == -1 and sel_plane == -1:
            self.targets[:] = 1
        elif sel_die == -1 and sel_plane != -1:
            self.targets[:, sel_plane] = 1
        elif sel_die != -1 and sel_plane == -1:
            self.targets[sel_die, :] = 1
        elif sel_die != -1 and sel_plane != -1:
            self.targets[sel_die, sel_plane] = 1

    def update(self):
        """
        모든 statetable 을 현재 시각 기준으로 update 하여 과거 state 제거
        """
        for idx in self.indice:
            self.statetable[idx].update()

    def step(self, time: float):
        """
        현재 시각에서 time 만큼 이동
        """
        self.clock.forward(time)

    def step_last(self):
        """
        statetable 에 등록된 seq.times 중 시간적으로 가장 짧은 시간만큼 clock 을 이동
        목적은 현재 등록된 모든 StateSeq 의 validity check 를 위함
        아직 확실하게 어떻게 써야할 지 정해지지 않음
        """
        idx_min = self.statetable.argmin(lambda x: x.get_lasttime())
        self.clock.init(self.statetable[idx_min].get_lasttime())

    def squeeze(self):
        """
        모든 statetable 에서 2번 이상 연속적으로 반복되는 state 원소들을 합침
        """
        for idx in self.indice:
            self.statetable[idx].squeeze()
