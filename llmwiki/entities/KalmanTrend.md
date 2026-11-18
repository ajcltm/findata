---
type: entity
sources: src/alpha/indicators/kalman.py L674-993; src/alpha/strategy/microstructure_watcher.py L40-88; reference/081_자체 칼만필터 모델.md 11장
updated: 2026-11-18
---
# KalmanTrend

`src/alpha/indicators/kalman.py`의 지표 클래스다. `ofi_norm`을 2상태 칼만으로 평활한다. 원리는 [[concepts/2상태 칼만 추세 추정기]]와 [[concepts/q R 분리 추정]]에 있다.

## 등록

```python
ofi_kf = KalmanTrend(source=flow, line="ofi_norm", dd_lag=self.p.kf_dd_lag)
self.ofi_kf = self.ind(ofi_kf, on="quote", symbol=sym, name="OFI_KF")
```
- [[entities/MicrostructureWatcher]](L87-88)가 등록한다. `kf_dd_lag` 기본값은 3.0이다 (L51). v0 `OFI_KF`의 `h`는 이 3.0초다.
- [[entities/BestQuoteFlow]] 뒤에 등록한다. 같은 quote 이벤트에서 상류가 먼저 갱신되어야 한다.

## 파라미터 (코드 L724-746)

| 분류 | 코드 | 기본값 | 근거 |
|---|---|---|---|
| 입력 | `source`, `line`, `src`, `lead_s` | `None`, `"value"`, `None`, 0.0 | `source`와 `src` 중 하나는 필수 |
| 본체 | `dd_lag` | 3.0초 | 합성 데이터의 $h^*$ (2.88) |
| 본체 | `q_halflife` | 60.0초 | 명세: CV 0.232, 음수율 0.00% |
| 본체 | `var_halflife` | 1800.0초 | 명세: CV 0.008 |
| 대조군 | `q_fixed`, `obs_var` | `None` | 고정값 실험용 |
| 안전장치 | `q_floor_ratio` | 1e-2 | Q=0이면 필터 동결 |
| 안전장치 | `r_floor_ratio` | 1e-3 | 이론상 $R \le A_h$ |
| 안전장치 | `min_n` | 30 | 창 키우기 최소 표본 |
| 안전장치 | `dup_line` | `"is_dup"` | 상류 중복 플래그 |
| 근거 없음 | `lag_tol_lo` / `lag_tol_hi` | 0.5 / 2.0 | 코드 L715: "제가 임의로 정한 값" |
| 근거 없음 | `nis_halflife` | 30.0초 | 같음 |
| 근거 없음 | `min_dt` | 1e-4초 | 같음 |

## 처리 순서 (`update()`)

1. 상류 `is_dup`이 참이면 직전 `level`을 돌려준다. 상태를 바꾸지 않는다.
2. `z`를 읽는다. `None`이면 직전 `level`을 돌려준다.
3. `_estimate()`로 두 `_Lag`에 표본을 넣고 $\hat q$, $\hat R$을 계산한다.
4. 준비가 안 되었으면 출력 라인을 `None`으로 비운다.
5. 첫 준비 틱이면 필터를 초기화한다.
6. 예측, 갱신, 진단을 한다.

출력 라인은 [[concepts/칼만 출력 라인과 진단]]에 있다.

## 관련 결정

- [[decisions/칼만 이상치 게이트 제거]]
- [[decisions/칼만 중복 스냅샷은 상류가 판정]]
- [[decisions/칼만 lag h는 고정]]

## v0 성능 (2026-10-23 mock 데이터)

- `h`=3초에서 `wf_med`는 18종목 중 12종목이 0이다. 최대값은 0.02648(005490)이다. q̂가 하한에 앉았다는 가설의 근거다.
- `kf_level`은 1분당 부호를 6.0번 바꾼다. `kf_slope`의 IC는 모든 보유 기간에서 0.03 미만이다.
- 자세한 내용: [[synthesis/2026-11-17_001_칼만 OFI v0 성능 분석 (2026-10-23 mock 데이터)]]

## 이력

- 예전 `microstructure.KalmanTrend`는 $q$를 고정 비율로 받고 $R$만 온라인 추정했다. 지금은 `src/legacy/microstructure_kalman.py`에 있다.
