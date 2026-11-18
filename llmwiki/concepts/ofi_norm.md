---
type: concept
sources: src/alpha/indicators/microstructure.py L269-363 (BestQuoteFlow docstring); reference/081_자체 칼만필터 모델.md 1장 기호표, 12장
updated: 2026-11-18
---
# ofi_norm

최우선호가 순유출량 `ofi`를 직전 최우선호가 잔량 합으로 나눈 값이다. [[entities/KalmanTrend]]의 관측 $z$로 쓴다.

## 정의 (microstructure.py L273-279)

- `ask_flow`: 매도 최우선호가에서 빠져나간 물량. 양수이면 상승 압력이다.
- `bid_flow`: 매수 쪽 같은 값. 양수이면 하락 압력이다.
- `ofi` = `ask_flow` − `bid_flow`. 양수이면 매수 압력이다. 단위는 '주'다.
- `ofi_norm` = `ofi` ÷ 직전 (ask1 잔량 + bid1 잔량).

## 알려진 한계

- 스냅샷 간 순유출이다. 원문 L344-346: "'주문 흐름'이 아니라 '스냅샷 간 순유출'이다."
- 분모가 분자와 맞지 않는다. 분모는 1호가 잔량이다. 분자는 여러 호가를 삼킬 수 있다. 이 문제는 고치지 않았다 (L349-354).
- 가격이 10호가 범위를 넘으면 값이 과소평가된다. 그때 `truncated` = 1이다.
- 정상 계열일 수 있다. 그러면 2상태 모델이 맞지 않는다. → [[concepts/칼만 실데이터 검증 순서]]
- 2026-10-23 mock 데이터 실측: 자기상관 lag1 0.004, lag5 0.006 (18종목 중앙값). 백색잡음에 가깝다. → [[synthesis/2026-11-17_001_칼만 OFI v0 성능 분석 (2026-10-23 mock 데이터)]]

## 연결

- 계산 지표: [[entities/BestQuoteFlow]]
- 다른 평활: `TimeEMA(30, flow, "ofi_norm")` (`microstructure_watcher.py`의 `OFI_EMA`).
- IC 대조군: 명세 12장은 "시간기반 EMA(파라미터 1개)"라 쓴다. 코드 L489는 "mid-EMA 대조군"이라 쓴다. 대조군이 `OFI_EMA`인지 mid 가격 EMA인지 원문에 없다.
