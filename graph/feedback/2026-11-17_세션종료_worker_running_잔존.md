# 세션이 끊기면 worker 실행 기록이 `running`으로 남는다

- 날짜: 2026-11-17
- 대상: `index/run/` 실행 기록, `.claude/hooks/activity.py`

## 현상

1. N-007 worker가 18:26에 시작했다.
2. 작업 도중 메인 세션이 끊겼다. worker도 함께 멈췄다.
3. 새 세션에서도 실행 기록 `index/run/20261117-182609-983440-N-007-worker.json`은 `state: running`으로 남았다.
4. `activity.py --watch`는 이 기록에 "멈춘 듯함" 경보를 낸다. 그래서 새 worker를 지켜보는 중에도 watch가 바로 돌아온다.

## 제안

- 세션이 시작될 때 훅이 이전 세션의 `running` 기록을 `stopped`(사유: 세션 종료)로 바꾼다.
- 또는 `activity.py`에 멈춘 기록을 정리하는 옵션을 둔다.
