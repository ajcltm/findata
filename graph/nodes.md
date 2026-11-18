# ⚙️ node

> 진행을 멈추고 확인하는 지점. 한 유스케이스의 node들은 worker 하나가 차례로 맡는다.

---

## ⚙️ N-001 지정 대상 코드 설명서 작성

- from: UC-001
- mode: execute
- pre: input: 대상과 모드 (스크립트 모드 또는 흐름 모드)
- do: 지정 대상 1개의 코드 설명서를 `code-flow-explainer` 스킬로 만든다. 기존 설명서가 있으면 바뀐 코드에 맞게 갱신한다
- out: out/code_docs/
- status: done

### 할 일
- 대상 코드를 읽는다
- 예시 2~4개와 예시 데이터를 정한다
- 예시 값을 코드 한 줄씩 따라간다
- 따라간 값으로 설명서 1개를 쓴다

---

## ⚙️ N-002 src 재분류안 작성

- from: UC-002
- mode: execute
- pre: none
- do: `src/`의 분류를 진단하고 새 폴더 구조와 옮길 목록을 만든다
- out: out/refactor/src_재분류안.md
- review: granted (2026-11-16, 607cb3d49f28)
- status: done

---

## ⚙️ N-003 src 코드 옮기기

- from: UC-002
- mode: build
- pre: N-002.out
- do: 승인한 재분류안대로 코드를 옮기고 import를 고친다
- out: src/
- status: done

### 할 일
- `out/refactor/src_재분류안.md`의 트리와 표대로 재분류한다

---

## ⚙️ N-007 칼만 OFI 버전 성능 분석

- from: UC-003
- mode: execute
- pre: input: 분석할 전략 버전
- do: 지정한 버전의 수집 데이터와 코드로 잡음과 추세 인식을 분석하고 분석서를 쓴다
- out: out/strategy_design/
- status: done

### 할 일
- 그 버전의 `OFI_KF` 데이터가 있는 최신일을 찾는다. DB는 읽기 전용으로 연다
- 잡음(1분당 부호 변경, 매끄러움), 예측력(IC), 진단 라인(`nis`, `world_frac`)을 계산한다
- 스크립트와 분석서(`kalman_ofi_prev_analysis.md`)를 `out/strategy_design/<분석할 버전>/analysis/`에 쓴다

---

## ⚙️ N-008 칼만 OFI 설계 방향서

- from: UC-003
- mode: execute
- pre: N-007.out, input: 전략 버전
- do: 분석서를 바탕으로 사용자와 설계방향을 논의하고 그 버전의 설계 방향서를 쓴다
- out: out/strategy_design/
- review: required
- status: todo

### 할 일
- 분석서를 읽고 방향 선택지와 질문을 정리해 프로젝트 매니저에게 돌려준다
- 사용자의 답을 받아 합의할 때까지 논의를 이어 간다
- 합의 내용을 `out/strategy_design/<버전>/kalman_ofi_direction.md`에 쓴다

---

## ⚙️ N-004 칼만 OFI 전략 설계

- from: UC-003
- mode: execute
- pre: N-008.out, input: 전략 버전
- do: 그 버전의 승인한 설계 방향서대로 전략과 지표를 md 설계서로 쓴다
- out: out/strategy_design/
- review: required
- status: todo

### 할 일
- 설계 방향서(`out/strategy_design/<버전>/kalman_ofi_direction.md`)를 읽는다. 방향서가 없으면 시작하지 않는다
- 방향서에 적힌 이번 사이클의 발전 목표를 따른다
- 필요한 지표와 출력 라인, 진입·청산·포지션 규칙을 정한다
- 설계서를 `out/strategy_design/<버전>/kalman_ofi_design.md`에 쓴다
- 설계서에 버전 번호와 이전 버전에서 바뀐 점을 적는다

---

## ⚙️ N-005 칼만 OFI indicator 코드

- from: UC-003
- mode: build
- pre: N-004.out, input: 전략 버전
- do: 그 버전의 승인한 설계서의 지표를 프로젝트 `Indicator` 인터페이스로 만든다
- out: src/alpha/indicators/
- status: todo

### 할 일
- 코드를 `src/alpha/indicators/kalman_ofi_<버전>.py`에 새로 만든다
- 이전 버전 파일과 `kalman.py`(`v0`)는 고치지 않는다
- 파일 머리 주석에 버전 번호와 설계서 경로를 적는다

---

## ⚙️ N-006 칼만 OFI strategy 코드

- from: UC-003
- mode: build
- pre: N-005.out, input: 전략 버전
- do: 그 버전의 승인한 설계서의 전략을 프로젝트 strategy 인터페이스로 만든다
- out: src/alpha/strategy/
- loop: from N-007, max 3
- status: todo

### 할 일
- 코드를 `src/alpha/strategy/kalman_ofi_<버전>.py`에 새로 만든다
- 같은 버전의 indicator(`kalman_ofi_<버전>.py`)만 쓴다
- 이전 버전 파일은 고치지 않는다
- 파일 머리 주석에 버전 번호와 설계서 경로를 적는다
