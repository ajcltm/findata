---
type: source
sources: out/strategy_design/v0/analysis/kalman_ofi_prev_analysis.md 1~7절, out/strategy_design/v0/analysis/diag.csv `wf_med` 열, N-007
updated: 2026-11-18
---
# v0 칼만 OFI 성능 분석서 (kalman_ofi_prev_analysis.md)

N-007(UC-003 M1)이 2026-11-17에 쓴 분석서다. v0 `OFI_KF`를 2026-10-23 mock 데이터로 평가했다. 결과와 해석은 [[synthesis/2026-11-17_001_칼만 OFI v0 성능 분석 (2026-10-23 mock 데이터)]]에 있다.

## 위치

- 폴더: `out/strategy_design/v0/analysis/`.
- `out/strategy_design/v1/analysis/`는 쓰지 않는다. 폴더를 지웠다 (2026-11-18 확인: 폴더 없음).
- 근거 규칙: [[decisions/성능 분석서는 분석 대상 버전 폴더에 둔다]]

| 파일 | 역할 (분석서 7절) |
|---|---|
| `kalman_ofi_prev_analysis.md` | 분석서 본문 |
| `extract.py` | DB에서 10-23 데이터를 뽑아 종목별 pkl로 저장한다 |
| `analyze.py` | pkl을 읽어 `ic.csv`, `diag.csv`를 만든다 |
| `ic.csv` | 종목 × 보유 기간 × 신호별 순위 IC (504행) |
| `diag.csv` | 종목별 잡음·진단 값 (18행) |
| `q.py`, `labels.py`, `schema.py`, `tables.py` | DB 조회 도구 (선택) |

> ⚠️ 재현할 때 DB는 `mode=ro`로만 연다. `src/kis/`와 `kis_broker.py`는 부르지 않는다.

## 분석 설정 (1절 표)

- DB: `D:/KIM/020_Project/260207.010_금융데이터/data/mock_data.db` (읽기 전용).
- 날짜: 2026-10-23. 종목: [[entities/MicrostructureWatcher]] 18종목.
- 시간: 09:05~15:20, 1초 격자.
- 칼만 설정: `dd_lag`(`h`) = 3.0초, `lead_s` = 0.0 (기본값).
- 칼만 입력: [[entities/BestQuoteFlow]]의 [[concepts/ofi_norm]] 라인 (`microstructure_watcher.py` L87).

## 핵심 수치

- `ofi_norm` 자기상관 중앙값: lag1 0.004, lag5 0.006.
- `wf_med` 중앙값 0.000. 18종목 중 12종목이 0이다. 최대값은 005490의 0.02648이다 (`diag.csv` 원값 0.02647670140890257).
- `nis_med` 중앙값 0.232, `nis_mean` 중앙값 0.820.
- 1분당 부호 변경: `kf_level` 6.0, `kf_slope` 6.9, `OFI_EMA(30s)` 1.0.
- 10초 평균 순위 IC: `kf_level` 0.114, `kf_slope` 0.024, `ofi_ema30` 0.091.

## 한계 (4절)

- 데이터는 하루치다.
- `h`는 3초 하나만 보았다.
- 거래 비용을 반영하지 않았다.
