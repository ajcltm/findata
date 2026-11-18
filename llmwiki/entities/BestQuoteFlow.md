---
type: entity
sources: src/alpha/indicators/microstructure.py L269-456
updated: 2026-11-17
---
# BestQuoteFlow

`src/alpha/indicators/microstructure.py`의 지표 클래스다. 호가 스냅샷(`H0STASP0`)에서 최우선호가 순유출량을 계산한다. [[entities/KalmanTrend]]의 상류다.

## 라인

| 라인 | 뜻 |
|---|---|
| `ask_flow`, `bid_flow` | 최우선호가에서 빠져나간 물량 |
| `ofi` | `ask_flow` − `bid_flow`. 단위 '주' |
| `ofi_norm` | 정규화한 `ofi`. → [[concepts/ofi_norm]] |
| `truncated` | 1이면 사다리 밖으로 나가 신뢰도가 낮다 |
| `is_dup` | 1이면 중복 스냅샷이다. 값 라인은 직전 행의 반복이다 |
| `dup_frac` | 중복 스냅샷 누적 비율. 원문: "녹음에서 제일 먼저 볼 것" |

## 중복 판정

- 이 클래스가 읽는 필드 전부를 비교한다: `best_ask`, `best_bid`, `ask_sizes[0]`, `bid_sizes[0]`, 두 사다리.
- 중복이면 값 라인을 건드리지 않는다. `is_dup` = 1만 세운다.
- 깊은 호가만 바뀌면 중복이 아니다. 그때 `ofi_norm` = 0을 낸다.
- 결정 근거: [[decisions/칼만 중복 스냅샷은 상류가 판정]]
