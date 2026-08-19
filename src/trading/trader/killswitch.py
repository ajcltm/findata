"""
═══════════════════════════════════════════════════════════════════
 killswitch.py — 파일 기반 비상정지
═══════════════════════════════════════════════════════════════════

    touch STOP_TRADING          →  1초 안에 모든 실주문 차단
    rm STOP_TRADING             →  원래 설정으로 복귀
    echo "이유" > STOP_TRADING  →  로그에 이유가 남는다

■ 왜 파일인가
    킬스위치의 핵심 요구는 '재시작해도 살아남는 것'이다.
    시그널이나 HTTP 로 건 정지는 프로세스가 죽으면 사라지는데,
    하필 이상 상황에서 프로세스가 죽는 경우가 많다. 자동 재기동되면
    아무도 모르게 거래가 재개된다. 파일은 지울 때까지 남는다.

    지연은 최대 1초다. 킬스위치에 그 정도는 문제되지 않는다 —
    더 급하면 프로세스를 죽이면 된다.

■ 안전 설계 세 가지
    ① 읽기 실패 시 '이전 상태 유지'
       디스크 오류로 파일을 못 읽었다고 거래가 켜지면 안 된다.
    ② 기동 시 파일이 있으면 크게 경고
       정지 파일을 안 지우고 재기동하면 하루 종일 dry-run 인데
       눈치 못 채는 게 이 방식의 유일한 실질적 약점이다.
    ③ 상태 변화가 있을 때만 로그
       매초 도배되면 정작 중요한 로그가 묻힌다.

■ 하지 않는 것
    포지션 청산. 새 주문만 막는다. 비상 상황에서 시장가 청산이
    더 큰 손실을 낼 수 있고, 청산 여부는 사람이 판단할 일이다.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

log = logging.getLogger("killswitch")

DEFAULT_PATH = "./STOP_TRADING"
CHECK_INTERVAL = 1.0        # 초. _periodic 이 이보다 자주 불려도 이 간격으로만 검사


class KillSwitch:
    """정지 파일을 감시해 Engine 의 실주문을 켜고 끈다.

    armed 는 '파일이 없을 때 어떤 상태로 돌아갈지'다.
        armed=True  기동 시 --real 로 실주문 모드였다 → 파일 지우면 재개
        armed=False 원래 dry-run 이었다 → 파일과 무관하게 계속 dry-run
    """

    def __init__(self, engine, path: str = DEFAULT_PATH, armed: bool = False):
        self.engine = engine
        self.path = Path(path)
        self.armed = armed

        self._last_check = 0.0
        self._blocked: bool | None = None    # 마지막으로 관측한 파일 존재 여부

        # ── 기동 시 1회 점검 ──
        # 초기 상태는 '전이'가 아니다. _blocked 를 미리 세워서
        # check() 가 "정지 파일 제거 확인" 같은 엉뚱한 로그를 찍지 않게 한다.
        self._blocked = self.path.exists()

        if self._blocked:
            # 이 경고가 없으면 '정지 파일을 안 지우고 재기동' 사고를 못 잡는다.
            # 이 방식의 유일한 실질적 약점이라 크게 찍는다.
            reason = self._read_reason()
            log.warning("=" * 56)
            log.warning(" 정지 파일이 이미 존재합니다: %s", self.path.resolve())
            log.warning(" 실주문이 차단된 상태로 기동합니다.%s",
                        f" (사유: {reason})" if reason else "")
            log.warning(" 거래를 시작하려면 이 파일을 지우세요.")
            log.warning("=" * 56)
            self.engine.disable_trading(reason="STOP_TRADING (기동 시)")
        elif not armed:
            log.info("dry-run 모드로 기동 — 실주문은 나가지 않습니다")

    def check(self, force: bool = False) -> None:
        """_periodic 이 매 루프 부른다. 내부에서 1초 간격으로만 실제 검사."""
        now = time.monotonic()
        if not force and now - self._last_check < CHECK_INTERVAL:
            return
        self._last_check = now

        try:
            blocked = self.path.exists()
            reason = self._read_reason() if blocked else ""
        except Exception:
            # ── 안전 설계 ① ──
            # 읽기 실패로 거래가 켜지면 안 된다. 이전 상태를 그대로 둔다.
            log.exception("정지 파일 확인 실패 — 이전 상태 유지")
            return

        if blocked == self._blocked:
            return                      # 상태 변화 없음. 로그도 안 남긴다
        self._blocked = blocked

        if blocked:
            self.engine.disable_trading(
                reason=f"STOP_TRADING{' — ' + reason if reason else ''}")
            log.warning("정지 파일 감지 — 실주문 차단 %s",
                        f"({reason})" if reason else "")
        else:
            log.warning("정지 파일 제거 확인")
            if self.armed:
                self.engine.enable_trading()
            else:
                log.info("armed=False 이므로 dry-run 을 유지합니다")

    def trip(self, reason: str = "programmatic"):
        """코드에서 직접 거는 비상정지. 리스크 한도 초과 등에 쓴다.

        파일을 만들어 두므로 재시작해도 유지된다 — 이게 중요하다.
        메모리 플래그만 세우면 재기동 시 사고가 그대로 재현된다."""
        try:
            self.path.write_text(reason, encoding="utf-8")
        except Exception:
            log.exception("정지 파일 생성 실패 — 메모리 차단만 적용")
        self.engine.disable_trading(reason=reason)
        self._blocked = True
        log.warning("비상정지 발동: %s", reason)

    def _read_reason(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8").strip()[:200]
        except Exception:
            return ""