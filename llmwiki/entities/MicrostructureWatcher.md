---
type: entity
sources: src/alpha/strategy/microstructure_watcher.py L13-16 L44-88, out/strategy_design/v0/analysis/kalman_ofi_prev_analysis.md 1절
updated: 2026-11-18
---
# MicrostructureWatcher

`src/alpha/strategy/microstructure_watcher.py`의 데이터 수집 전략이다. 미시구조 지표를 갱신하고 기록한다. 주문은 내지 않는다 (L45-46 docstring).

## 파라미터 (`defaults`, L48-57)

| 코드 | 기본값 | 뜻 |
|---|---|---|
| `symbol` | `"005930"` | 종목. 종목마다 인스턴스를 따로 만든다 |
| `ema_halflife` | 30.0 | `TimeEMA` 반감기(초) |
| `kf_dd_lag` | 3.0 | [[entities/KalmanTrend]]의 `h`(초). 세 점 거리 |
| `imb_levels` | 3 | `QueueImbalance` 깊이 |
| `depletion_window` | 60.0 | `DepletionRate` 창(초) |
| `tfi_window` | 30.0 | `TradeFlowImbalance` 창(초) |

- `kf_dd_lag` 주석 원문 (L51-55): "합성데이터 기준값. 녹음에서 world_frac 보고 조정할 것".
- v0 `OFI_KF`의 `h`는 이 `kf_dd_lag` = 3.0초다. → [[decisions/칼만 lag h는 고정]]

## 칼만 OFI 등록 (L70-88)

1. `BestQuoteFlow`를 `name="BestQuoteFlow"`로 등록한다 (L70-71).
2. `TimeEMA(ema_halflife, flow, "ofi_norm")`를 `OFI_EMA(30s)`로 등록한다 (L75-77).
3. `KalmanTrend(source=flow, line="ofi_norm", dd_lag=self.p.kf_dd_lag)`를 `name="OFI_KF"`로 등록한다 (L87-88).

- 칼만 입력은 [[entities/BestQuoteFlow]]의 [[concepts/ofi_norm]]이다.
- `OFI_KF`는 `flow` 뒤에 등록해야 한다. 원문 L81: "반드시 flow 뒤에 등록."
- 라벨은 종목 접미사 없이 고정한다. 이유: Pivot 뷰의 칼럼이 지표×종목마다 갈라진다 (L60-64).

## v0 성능

- 2026-10-23 mock 데이터 18종목 분석: [[synthesis/2026-11-17_001_칼만 OFI v0 성능 분석 (2026-10-23 mock 데이터)]]
- 분석서 요약: [[sources/kalman_ofi_prev_analysis]]
