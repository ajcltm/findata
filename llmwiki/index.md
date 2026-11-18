# llmwiki 색인

- 조회는 여기서 시작한다.
- 페이지 하나에 한 줄을 쓴다.
- librarian이 관리한다.

## overview
- [[overview]] — 프로젝트 전체 요약

## sources
- [[sources/081_자체 칼만필터 모델]] — 2상태 칼만 추세 추정기 수학 명세 (사용자 문서, 코드보다 앞 판)
- [[sources/kalman.py]] — `src/alpha/indicators/kalman.py` 코드 요약 (현재 판 `KalmanTrend`)
- [[sources/kalman_ofi_prev_analysis]] — v0 칼만 OFI 성능 분석서(N-007). 위치, 재현 파일, 핵심 수치

## concepts
- [[concepts/2상태 칼만 추세 추정기]] — 모델, Q 행렬, 칼만 재귀, 초기화, 출력
- [[concepts/q R 분리 추정]] — 세 점 조합, 두 lag, 두 반감기, 창 키우기, 하한
- [[concepts/칼만 출력 라인과 진단]] — 라인 10개, nis, 하한 점검
- [[concepts/칼만 실데이터 검증 순서]] — 녹음 하루 검증 8단계와 미해결 항목
- [[concepts/ofi_norm]] — 칼만 입력. 최우선호가 순유출 정규화 값과 한계

## entities
- [[entities/KalmanTrend]] — 칼만 지표 클래스. 등록, 파라미터, 처리 순서
- [[entities/BestQuoteFlow]] — `ofi_norm`을 만드는 상류 지표. `is_dup`, `dup_frac`
- [[entities/MicrostructureWatcher]] — 칼만 OFI를 등록하는 수집 전략. `kf_dd_lag`=3.0, 입력 `ofi_norm`

## decisions
- [[decisions/칼만 이상치 게이트 제거]] — R 추정 게이트를 없앤 근거와 되돌릴 조건
- [[decisions/칼만 중복 스냅샷은 상류가 판정]] — `repeat_mode`를 `is_dup` 플래그로 바꾼 근거
- [[decisions/칼만 lag h는 고정]] — `dd_lag`를 장중에 바꾸지 않는 근거
- [[decisions/성능 분석서는 분석 대상 버전 폴더에 둔다]] — 분석서는 `out/strategy_design/<버전>/analysis/`에 둔다

## synthesis

- [[synthesis/2026-11-17_001_칼만 OFI v0 성능 분석 (2026-10-23 mock 데이터)]] — v0 칼만 OFI는 하루치 mock 데이터에서 잡음이 크고 추세를 못 읽는다. 입력·모델 불일치와 q̂ 하한이 원인 가설이다.
