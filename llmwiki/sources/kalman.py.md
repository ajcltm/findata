---
type: source
sources: src/alpha/indicators/kalman.py L1-511 (docstring), L527-552 (three_point), L559-667 (_Lag), L674-993 (KalmanTrend)
updated: 2026-11-17
---
# src/alpha/indicators/kalman.py

데이터 수집에 쓰는 2상태 칼만 지표 `KalmanTrend`의 코드다. librarian은 이 코드를 읽기만 했다. 실행하지 않았다.

## 구성

| 위치 | 이름 | 하는 일 |
|---|---|---|
| L527-552 | `three_point()` | 세 점에서 $D$, $W$, $G$를 계산한다 |
| L559-667 | `_Lag` | lag 하나의 삼중항 버퍼와 fast, slow EMA 두 개를 갖는다 |
| L674-993 | `KalmanTrend(Indicator)` | $\hat q$, $\hat R$을 추정하고 칼만 필터를 돌린다 |

## 사용처

- `src/alpha/strategy/microstructure_watcher.py` L87: `KalmanTrend(source=flow, line="ofi_norm", dd_lag=self.p.kf_dd_lag)`. 이름은 `OFI_KF`다.
- 입력 `flow`는 [[entities/BestQuoteFlow]]다. `flow`를 먼저 등록해야 한다.
- 예전 클래스는 `src/legacy/microstructure_kalman.py`에 남아 있다. 예전 클래스는 $q$를 고정 비율로 받았다.
- `MicrostructureWatcher`는 주문을 내지 않는다. 원문 L47: "갱신·기록한다. 주문은 내지 않는다."

## 핵심 내용

- docstring 요약 (L11-12): "세상이 움직이는 속도(q)와 센서가 틀리는 정도(R)를 분리해서 재고, 둘의 비로 "관측을 얼마나 믿을지"를 매 틱 정한다."
- 이상치 게이트를 없앴다 (L298-352). → [[decisions/칼만 이상치 게이트 제거]]
- 중복 판정을 상류 `is_dup`에 맡겼다 (L355-401). → [[decisions/칼만 중복 스냅샷은 상류가 판정]]
- `dd_lag`($h$)는 고정한다. $h$를 $h^*$로 따라가게 하면 추정이 터진다 (L158-172). → [[decisions/칼만 lag h는 고정]]
- 대조군용 고정값 `q_fixed`, `obs_var`가 있다 (L735-736).

## 문서와 다른 점

- 수학 명세와 다른 점은 [[sources/081_자체 칼만필터 모델]] "코드와 다른 점" 표에 있다.
- 코드에서 fast, slow EMA는 표본 수 `n`을 같이 쓴다 (L593). 명세 의사코드는 `n_fast`, `n_slow`를 따로 센다 (11장).
- 코드의 `surprise` 분모 $\hat V$ EMA에는 창 키우기가 없다 (L945-947). `nis` 평균에는 있다 (L970).
