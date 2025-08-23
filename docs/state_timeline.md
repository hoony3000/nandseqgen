## State Timeline 설계

- phase_timeline → state_timeline 전환.
- pos(START/MID/END) 제거. 분포 키는 `OP.STATE` 또는 `OP.END`.
- 기준은 `op_specs.states`의 state 목록. 예약 시 절대 구간 기록.
- 마지막 state 이후에는 `OP.END`를 `end_us = inf`로 유지하다가, 새 operation 예약 시 이전 `OP.END`를 잘라내고 새로운 `OP.END`로 교체.
- 일부 operation은 타임라인에 영향 없음(예: `SR`): CFG로 per-op 제어(`policy.state_timeline.affects`).
- 아직 스케줄되지 않은 obligation은 타임라인에 기록하지 않음.
- `exclusion window`와 `state_timeline`은 동일 데이터 구조로 통합 가능(본 단계에서는 별도 유지, 이후 `overlaps` 질의로 대체 예정).

### 데이터 구조

StateTimeline는 (die, plane) 별 정렬된 `StateInterval` 리스트를 보유.

```
StateInterval: { die, plane, op, state, start_us, end_us }
```

API 스케치:
- `reserve_op(die, plane, op_name, states, start_us, affect)`
- `state_at(die, plane, t) -> Optional[str]  # 'OP.STATE'`
- `overlaps(die, plane, start, end, pred=None) -> bool`

### 통합 포인트

- Scheduler._schedule_operation: 예약 성공 시 각 타겟에 대해 `reserve_op(...)` 호출.
- PolicyEngine.propose: phase-conditional 단계에서 `state_at(die, plane, t_ref)`로 상태 키 계산 후 분포 선택.
- CFG:

```yaml
policy:
  state_timeline:
    affects:
      READ: true
      PROGRAM: true
      ERASE: true
      DOUT: true
      SR: false
```


