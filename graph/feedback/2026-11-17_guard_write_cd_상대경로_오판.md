# guard_write 훅이 `cd` 뒤의 상대 경로를 막는다

- 날짜: 2026-11-17
- 대상: `.claude/hooks/` guard_write 훅

## 현상

1. N-007 worker가 `cd .../out/strategy_design && rm v1/analysis/...`를 실행했다.
2. 지울 파일은 node의 결과물 경로(`out/strategy_design/`) 안에 있다.
3. 훅은 `v1/analysis/...`를 결과물 경로로 알아보지 못하고 막았다.
4. MEMO-011(셸 변수 `$A`를 풀지 못함)과 같은 종류의 오판이다.

## 영향

- worker가 허용된 파일 이동과 삭제를 끝내지 못한다. 매니저가 대신 지웠다.
- `cp`, `mv`도 막힐 수 있어 worker는 Write로 다시 쓴다. 20KB 파일 하나에 약 70초가 걸린다.

## 제안

- 훅이 `cd` 대상 경로를 기준으로 상대 경로를 푼다.
- 결과물 경로 안의 `cp`, `mv`, `rm`은 허용한다.
