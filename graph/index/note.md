# note (자동 생성 — 직접 수정 금지)

생성: 2026-11-18T17:24:48

## N-001 지정 대상 코드 설명서 작성
- **MEMO-003** (failure, open, cause:hook) `guard_write.py`가 Bash 명령 안 문자열의 `>`와 `sed` 치환식을 쓰기 경로로 판정했다 (2회). worker는 파일을 Write·Edit 도구로만 쓰고 고친다. Bash로는 읽기와 스크립트 실행만 한다.
- **MEMO-001** (request, resolved) 대상은 `src/alpha/analysist/tdata.py` (Tdata 클래스)다. 사용자가 2026-11-14에 골랐다.
- **MEMO-002** (request, resolved) 대상은 `src/alpha/engine/engine.py`다. 모드는 스크립트 모드다. 사용자가 2026-11-16에 골랐다 (IN-005).
- **MEMO-007** (request, resolved) `engine_코드설명서.md`의 경로와 줄 번호를 재분류 뒤 구조(커밋 `de85e88`)에 맞게 고친다. 예: `trading.py` → `core/orders.py`, `trader/trader.py`. 설명 내용은 다시 쓰지 않는다. 대상은 그대로 `engine.py`다.
- **MEMO-008** (request, resolved) `engine_코드설명서.md`의 줄 번호 오류 2곳을 고친다. (1) `apply_fill()` 설명이 `is_done` 줄(`core/orders.py:100`)을 가리킨다 → `apply_fill`(103행)과 FILLED를 정하는 줄(118–119행)로 고친다. (2) `view_q`에 넣는 코드라는 참조가 지표 목록 코드(`strategy/base.py:191-194`)를 가리킨다 → 실제로 `view_q`에 넣는 줄을 찾아 고친다. 찾으면 "(추정)"을 뗀다. 다른 내용은 바꾸지 않는다. hash:9f6784800b95

## N-002 src 재분류안 작성
- **MEMO-004** (request, resolved) 재분류안은 짧게 쓴다. 새 폴더 트리와 "옛 위치 → 새 위치" 표만 쓴다. 설명 글은 쓰지 않는다.

## N-003 src 코드 옮기기
- **MEMO-005** (note, archived) 함수 안 지연 import(`alphatrader.py` 491행, `recorder.py` 296행)도 새 경로로 고친다. `kis_broker.py`·`bt_broker.py`의 쓰지 않는 `Strategy`, `Trader` import는 지우지 않고 경로만 바꾼다.
- **MEMO-006** (note, archived) 사용자가 커밋하지 않은 변경이 있다 (`src/alpha/indicators/kalman.py`, notebook 2개). 이 파일들은 고치지 않는다. 작업 브랜치는 `refactor/src-reclass`다. worker는 커밋하지 않는다.

## N-007 칼만 OFI 버전 성능 분석
- **MEMO-010** (note, open) N-007: Bash의 `python`은 Windows 스토어 스텁이다. 실행하면 exit 49로 끝난다. `C:/Users/USER/AppData/Local/Programs/Python/Python312/python.exe` 전체 경로를 써야 한다. / (worker 보고에서 자동 기록)
- **MEMO-011** (note, open) N-007: guard_write 훅은 Bash 명령 안의 셸 변수(`$A`)를 풀지 못한다. 그래서 허용된 경로도 막는다. / (worker 보고에서 자동 기록)
- **MEMO-012** (note, open) N-007: 작업 폴더에 집계 스크립트 `index/work/N-007/summ.py`가 남아 있다. csv만 읽는 스크립트다. / (worker 보고에서 자동 기록)
- **MEMO-013** (note, open) N-007: 앞 실행은 보고의 `막힘:` 칸 아래에 훅 차단 기록을 불릿으로 붙였다. 그래서 훅이 node를 todo로 되돌렸다. 결과물 자체에는 문제가 없었다. 앞으로 훅 차단 같은 참고 사항은 `남길 메모:`에 쓴다. / (worker 보고에서 자동 기록)
- **MEMO-014** (note, open) N-007: 분석서 7절 재현 명령은 `python`을 쓴다. 이 PC의 Bash에서는 MEMO-010의 전체 경로를 써야 한다. / (worker 보고에서 자동 기록)
- **MEMO-015** (note, open) 프레임워크: 훅은 SubagentHandback 보고를 읽지 못하는 것으로 보인다. 훅은 마지막 텍스트 응답에서 칸을 읽는다. `framework-feedback/`에 기록할 거리다. / (worker 보고에서 자동 기록)
- **MEMO-016** (note, open) N-007: v1 원본 9개 파일과 빈 폴더 `v1/analysis/`, `v1/`는 매니저가 절대 경로로 지웠다. 매니저는 지우기 전에 내용이 같음을 확인했다. / (worker 보고에서 자동 기록)
- **MEMO-017** (note, open) 앞서 내 `rm`은 상대 경로였다. 명령은 `cd .../out/strategy_design && rm v1/analysis/... && rmdir ...`였다. guard_write 훅이 이 명령을 막았다. / (worker 보고에서 자동 기록)
- **MEMO-018** (note, open) 훅 메시지 원문: "v1/analysis/kalman_ofi_prev_analysis.md: N-007의 worker는 맡은 node의 결과물(out/strategy_design/)과 작업 폴더(index/work/N-007/)에만 쓴다. 우회하지 말고 멈춘 뒤 '막힘'으로 보고한다." / (worker 보고에서 자동 기록)
- **MEMO-019** (note, open) N-007: v0의 `ic.csv`, `diag.csv`는 줄 끝이 LF다. 원본은 CRLF였다. 다시 만든 csv와 비교할 때는 `diff --strip-trailing-cr`를 쓴다. `cmp`는 다르다고 판정한다. / (worker 보고에서 자동 기록)
- **MEMO-020** (note, open) 프레임워크: guard_write 훅이 `cd` 뒤의 상대 경로를 허용 경로로 알아보지 못하는 것 같다. 확인하지는 않았다. MEMO-011과 같은 종류의 문제다. `framework-feedback/`에 적을 거리다. / (worker 보고에서 자동 기록)

## N-004 칼만 OFI 전략 설계
- **MEMO-009** (request, open) 사용자가 2026-11-17에 요청했다 (IN-012, IN-013). 기존 M1은 현재 `kalman.py`에 너무 맞춰져 있다. 프로젝트 매니저가 사용자와 설계방향을 충분히 검토한 뒤 설계 방향서(`out/strategy_design/vN/kalman_ofi_direction.md`)를 만든다. N-004는 그 방향서를 입력으로 받는다.
