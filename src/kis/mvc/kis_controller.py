"""
컨트롤러 — 파싱 1회, 렌더 스레드 사망 방지, 입력 논블로킹.

스레드 구성
    파서 스레드   raw_q → 파싱 1회 → view_q / record_q / trade_q 로 팬아웃
    렌더 스레드   view_q 소진 → TickState 갱신 → 화면 1장
    메인 스레드   input() 대기. 느린 작업은 절대 여기서 하지 않는다.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

log = logging.getLogger("kis.ui")


class ParserWorker:
    """
    ⚠️ 기존 구조는 recording·trading·view가 각자 파싱했다. 같은 틱을
       세 번, 체결통보는 AES 복호화까지 세 번이다. 파싱을 앞으로 당기고
       결과를 나눠주면 한 번으로 끝난다.
    """

    def __init__(self, raw_q, parser, out_queues: dict[str, queue.Queue]):
        self.raw_q = raw_q
        self.parser = parser
        self.out_queues = out_queues
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="parser",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                tick = self.raw_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                parsed = self.parser.parse(tick)
                if parsed is None:            # 파서는 실패 시 None을 준다
                    continue
                for name, q in self.out_queues.items():
                    try:
                        q.put_nowait(parsed)
                    except queue.Full:
                        log.warning("[%s] 큐 포화, 드롭", name)
            except Exception:
                log.exception("파서 스레드 예외, 건너뜀")
            finally:
                self.raw_q.task_done()


class ScreenManager:
    def __init__(self, ctx, view_q, screens, parser, start="home", interval=1.0, on_quit=None):
        self.ctx = ctx
        self.view_q = view_q
        self.screens = {s.name: s for s in screens}
        self.parser = parser
        self.current = self.screens[start]
        self.interval = interval
        self.on_quit = on_quit
        self._stop = threading.Event()

    # ── 실행 ───────────────────────────────────────────────────
    def start(self) -> None:
        threading.Thread(target=self._render_loop, name="render",
                         daemon=True).start()
        self._input_loop()

    # ── 렌더 ───────────────────────────────────────────────────
    def _render_loop(self) -> None:
        while not self._stop.is_set():
            # ⚠️ 이 try가 없으면 파싱 실패 한 건에 렌더 스레드가 죽는다.
            #    daemon=True라 프로그램은 살아 있고 화면만 그 순간에 멈춘다.
            #    사용자는 장이 조용한 줄 알게 된다 — 최악의 실패 방식이다.
            try:
                self._drain()
                self.current.render(self.ctx)
            except Exception:
                log.exception("렌더 실패, 다음 프레임에서 재시도")
            self._stop.wait(self.interval)     # sleep보다 종료가 즉시 먹는다

    def _drain(self, max_items: int = 5000) -> None:
        """큐는 흐름, 화면은 순간. 한 프레임에 쌓인 만큼을 접는다."""
        for _ in range(max_items):
            try:
                tick = self.view_q.get_nowait()
            except queue.Empty:
                return
            try:
                parsed = self.parser.parse(tick)
                if parsed is None:
                    continue
                self.ctx.ticks.on_parsed(parsed)
            finally:
                self.view_q.task_done()

    # ── 입력 ───────────────────────────────────────────────────
    def _input_loop(self) -> None:
        while not self._stop.is_set():
            try:
                k = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                self._quit()
                break

            if k == "h":
                self.current = self.screens["home"]
            elif k == "r":
                self.current = self.screens["realdata"]
            elif k == "o":
                # 화면부터 바꾸고 조회는 뒤로 던진다. 여기서 REST를 부르면
                # 응답이 올 때까지 입력이 멈춰, 눌러도 반응 없는 것처럼 보인다.
                self.current = self.screens["orders"]
                self._fetch_orders_async()
            elif k in ("j", "k") and hasattr(self.current, "scroll"):
                self.current.scroll += self.current.page_size if k == "j" \
                    else -self.current.page_size
            elif k == "q":
                self._quit()
                break

    def _quit(self) -> None:
        """렌더/입력 루프를 세우고, 엔진(웹소켓 등)도 같이 종료시킨다."""
        self._stop.set()
        print("\n종료합니다...")
        if self.on_quit is not None:
            try:
                self.on_quit()
            except Exception:
                log.exception("종료 콜백 실패")