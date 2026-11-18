---
type: overview
sources: reference/081_자체 칼만필터 모델.md, src/alpha/indicators/kalman.py, src/alpha/strategy/microstructure_watcher.py
updated: 2026-11-18
---
# 프로젝트 개요

이 저장소는 사용자가 개발 중인 트레이딩 프로그램이다. 사용자는 기존 코드를 하나씩 이해하고 싶어 한다.

## 칼만필터 OFI (REQ-003 근거)

- 데이터 수집 전략 [[entities/MicrostructureWatcher]]가 [[entities/BestQuoteFlow]]의 [[concepts/ofi_norm]]을 [[entities/KalmanTrend]]로 평활한다. 이 전략은 주문을 내지 않는다.
- 필터 원리: [[concepts/2상태 칼만 추세 추정기]], [[concepts/q R 분리 추정]]
- 명세([[sources/081_자체 칼만필터 모델]])는 코드([[sources/kalman.py]])보다 앞 판이다. 차이는 decisions에 있다.
- 필터는 2026-10-23 mock 데이터 하루, `h`=3초로만 평가했다. `kf_level`은 잡음이 크고 `kf_slope`는 예측력이 낮다. → [[synthesis/2026-11-17_001_칼만 OFI v0 성능 분석 (2026-10-23 mock 데이터)]]
- 남은 검증: [[concepts/칼만 실데이터 검증 순서]]
- 성능 분석서는 분석 대상 버전 폴더에 둔다. v0 분석서: `out/strategy_design/v0/analysis/`. → [[decisions/성능 분석서는 분석 대상 버전 폴더에 둔다]]
- 명세와 코드 어디에도 진입, 청산 규칙은 없다.
