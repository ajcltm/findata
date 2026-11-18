# 🗒️ 메모

> 요청·실패·참고 메모. target이 대상 문서다. 처리하면 아래에 처리 줄을 붙이고 status를 바꾼다.

> [!memo] MEMO-001 type:request target:N-001 status:resolved
> 대상은 `src/alpha/analysist/tdata.py` (Tdata 클래스)다. 사용자가 2026-11-14에 골랐다.
> - resolved: 2026-11-16 by:project_manager → 설명서를 만들기 전에 사용자가 대상을 `engine.py`로 바꿨다 hash:01d91083c8e4

> [!memo] MEMO-002 type:request target:N-001 status:resolved
> 대상은 `src/alpha/engine/engine.py`다. 모드는 스크립트 모드다. 사용자가 2026-11-16에 골랐다 (IN-005).
> - resolved: 2026-11-16 by:worker → `out/code_docs/engine_코드설명서.md`를 만들었다 hash:96ce88586146

> [!memo] MEMO-003 type:failure target:N-001 status:open cause:hook
> `guard_write.py`가 Bash 명령 안 문자열의 `>`와 `sed` 치환식을 쓰기 경로로 판정했다 (2회). worker는 파일을 Write·Edit 도구로만 쓰고 고친다. Bash로는 읽기와 스크립트 실행만 한다.

> [!memo] MEMO-004 type:request target:N-002 status:resolved
> 재분류안은 짧게 쓴다. 새 폴더 트리와 "옛 위치 → 새 위치" 표만 쓴다. 설명 글은 쓰지 않는다.
> - resolved: 2026-11-16 by:worker → 트리 1개와 표 1개로 썼다 hash:7b36fdcceb51

> [!memo] MEMO-005 type:note target:N-003 status:archived
> 함수 안 지연 import(`alphatrader.py` 491행, `recorder.py` 296행)도 새 경로로 고친다. `kis_broker.py`·`bt_broker.py`의 쓰지 않는 `Strategy`, `Trader` import는 지우지 않고 경로만 바꾼다.
> - archived: 2026-11-16 by:worker → 반영했다

> [!memo] MEMO-006 type:note target:N-003 status:archived
> 사용자가 커밋하지 않은 변경이 있다 (`src/alpha/indicators/kalman.py`, notebook 2개). 이 파일들은 고치지 않는다. 작업 브랜치는 `refactor/src-reclass`다. worker는 커밋하지 않는다.
> - archived: 2026-11-16 by:project_manager → worker는 고치지 않았다. 그러나 훅의 자동 커밋(de85e88)에 `kalman.py`의 사용자 변경(공백 1칸)이 함께 들어갔다

> [!memo] MEMO-007 type:request target:N-001 status:resolved
> `engine_코드설명서.md`의 경로와 줄 번호를 재분류 뒤 구조(커밋 `de85e88`)에 맞게 고친다. 예: `trading.py` → `core/orders.py`, `trader/trader.py`. 설명 내용은 다시 쓰지 않는다. 대상은 그대로 `engine.py`다.
> - resolved: 2026-11-16 by:worker → 경로 19곳, 줄 번호 117개를 고쳤다. 옛 경로 0건 hash:9f6784800b95

> [!memo] MEMO-008 type:request target:N-001 status:resolved
> `engine_코드설명서.md`의 줄 번호 오류 2곳을 고친다. (1) `apply_fill()` 설명이 `is_done` 줄(`core/orders.py:100`)을 가리킨다 → `apply_fill`(103행)과 FILLED를 정하는 줄(118–119행)로 고친다. (2) `view_q`에 넣는 코드라는 참조가 지표 목록 코드(`strategy/base.py:191-194`)를 가리킨다 → 실제로 `view_q`에 넣는 줄을 찾아 고친다. 찾으면 "(추정)"을 뗀다. 다른 내용은 바꾸지 않는다. hash:9f6784800b95
> - resolved: 2026-11-16 by:worker → 2곳을 고쳤다. view_q 동작은 확인해서 "(추정)"을 뗐다. "지표마다 라인 1개"는 확인하지 못해 추정으로 남겼다 hash:0347dc90a515

> [!memo] MEMO-009 type:request target:N-004 status:open
> 사용자가 2026-11-17에 요청했다 (IN-012, IN-013). 기존 M1은 현재 `kalman.py`에 너무 맞춰져 있다. 프로젝트 매니저가 사용자와 설계방향을 충분히 검토한 뒤 설계 방향서(`out/strategy_design/vN/kalman_ofi_direction.md`)를 만든다. N-004는 그 방향서를 입력으로 받는다.
> - 2026-11-17 추가 요청: "바퀴"를 "사이클"로 바꾼다. 사이클마다 버전 번호를 붙인다. 설계서, 지표, 전략은 같은 버전 번호를 쓴다.

> [!memo] MEMO-010 type:note target:N-007 status:open
> N-007: Bash의 `python`은 Windows 스토어 스텁이다. 실행하면 exit 49로 끝난다. `C:/Users/USER/AppData/Local/Programs/Python/Python312/python.exe` 전체 경로를 써야 한다.
> (worker 보고에서 자동 기록)

> [!memo] MEMO-011 type:note target:N-007 status:open
> N-007: guard_write 훅은 Bash 명령 안의 셸 변수(`$A`)를 풀지 못한다. 그래서 허용된 경로도 막는다.
> (worker 보고에서 자동 기록)

> [!memo] MEMO-012 type:note target:N-007 status:open
> N-007: 작업 폴더에 집계 스크립트 `index/work/N-007/summ.py`가 남아 있다. csv만 읽는 스크립트다.
> (worker 보고에서 자동 기록)

> [!memo] MEMO-013 type:note target:N-007 status:open
> N-007: 앞 실행은 보고의 `막힘:` 칸 아래에 훅 차단 기록을 불릿으로 붙였다. 그래서 훅이 node를 todo로 되돌렸다. 결과물 자체에는 문제가 없었다. 앞으로 훅 차단 같은 참고 사항은 `남길 메모:`에 쓴다.
> (worker 보고에서 자동 기록)

> [!memo] MEMO-014 type:note target:N-007 status:open
> N-007: 분석서 7절 재현 명령은 `python`을 쓴다. 이 PC의 Bash에서는 MEMO-010의 전체 경로를 써야 한다.
> (worker 보고에서 자동 기록)

> [!memo] MEMO-015 type:note target:N-007 status:open
> 프레임워크: 훅은 SubagentHandback 보고를 읽지 못하는 것으로 보인다. 훅은 마지막 텍스트 응답에서 칸을 읽는다. `framework-feedback/`에 기록할 거리다.
> (worker 보고에서 자동 기록)

> [!memo] MEMO-016 type:note target:N-007 status:open
> N-007: v1 원본 9개 파일과 빈 폴더 `v1/analysis/`, `v1/`는 매니저가 절대 경로로 지웠다. 매니저는 지우기 전에 내용이 같음을 확인했다.
> (worker 보고에서 자동 기록)

> [!memo] MEMO-017 type:note target:N-007 status:open
> 앞서 내 `rm`은 상대 경로였다. 명령은 `cd .../out/strategy_design && rm v1/analysis/... && rmdir ...`였다. guard_write 훅이 이 명령을 막았다.
> (worker 보고에서 자동 기록)

> [!memo] MEMO-018 type:note target:N-007 status:open
> 훅 메시지 원문: "v1/analysis/kalman_ofi_prev_analysis.md: N-007의 worker는 맡은 node의 결과물(out/strategy_design/)과 작업 폴더(index/work/N-007/)에만 쓴다. 우회하지 말고 멈춘 뒤 '막힘'으로 보고한다."
> (worker 보고에서 자동 기록)

> [!memo] MEMO-019 type:note target:N-007 status:open
> N-007: v0의 `ic.csv`, `diag.csv`는 줄 끝이 LF다. 원본은 CRLF였다. 다시 만든 csv와 비교할 때는 `diff --strip-trailing-cr`를 쓴다. `cmp`는 다르다고 판정한다.
> (worker 보고에서 자동 기록)

> [!memo] MEMO-020 type:note target:N-007 status:open
> 프레임워크: guard_write 훅이 `cd` 뒤의 상대 경로를 허용 경로로 알아보지 못하는 것 같다. 확인하지는 않았다. MEMO-011과 같은 종류의 문제다. `framework-feedback/`에 적을 거리다.
> (worker 보고에서 자동 기록)
