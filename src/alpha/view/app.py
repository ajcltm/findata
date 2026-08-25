"""라우터 + 런타임 + 파사드.

  Router       화면 스택과 전역 키.
  Runtime      스레드·큐·출력. MVC 바깥의 인프라.
  Application  컨트롤러가 보는 얼굴. goto / back / submit / ctx.

스레드 규칙 (이거 하나면 락이 필요 없다)
  모델 변경은 전부 렌더 스레드에서. 입력 스레드는 라우팅과 submit만,
  워커 스레드는 순수 I/O만.
"""

from __future__ import annotations

import logging
import queue
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

from alpha.view.view import frame

log = logging.getLogger(__name__)


class Router:
    GLOBAL_KEYS = {
        "h": lambda app, arg: app.goto("home"),
        "r": lambda app, arg: app.goto("realdata"),
        "o": lambda app, arg: app.goto("orders"),
        "v": lambda app, arg: app.goto("feed"),
        "b": lambda app, arg: app.back(),
        "?": lambda app, arg: app.ctx.flash(app.help_text()),
        "q": lambda app, arg: app.quit(),
    }

    def __init__(self, controllers, start="home"):
        self.controllers = {c.name: c for c in controllers}
        self._stack = [self.controllers[start]]

    @property
    def current(self):
        return self._stack[-1]

    def goto(self, app, name, replace=True, **opts):
        ctrl = self.controllers.get(name)
        if ctrl is None:
            app.ctx.flash(f"없는 화면: {name}")
            return
        if ctrl is self.current and replace:
            return
        if replace:
            self._stack.pop().on_exit()
        self._stack.append(ctrl)
        ctrl.on_enter(app, **opts)

    def back(self, app):
        if len(self._stack) == 1:
            app.ctx.flash("첫 화면입니다.")
            return
        self._stack.pop().on_exit()

    def resolve(self, key):
        """화면 키가 전역 키를 가린다."""
        return self.current.keymap.get(key) or self.GLOBAL_KEYS.get(key)

    def help_text(self) -> str:
        parts = [f"{k}:{(fn.__doc__ or '').strip() or '?'}"
                 for k, fn in self.current.keymap.items()]
        parts += ["h:홈", "r:시세", "o:주문", "v:구독", "b:뒤로", "q:종료"]
        return " ".join(parts)


class Runtime:
    """스레드 3종. 여기만 print를 한다."""

    def __init__(self, app, view_q, interval=1.0, workers=4, on_quit=None):
        self.app = app
        self.view_q = view_q
        self.interval = interval
        self.on_quit = on_quit
        self._result_q = queue.Queue()
        self._pool = ThreadPoolExecutor(workers, thread_name_prefix="task")
        self._stop = threading.Event()
        self._wake = threading.Event()

    def start(self) -> None:
        self.app.router.current.on_enter(self.app)
        threading.Thread(target=self._render_loop, name="render",
                         daemon=True).start()
        self._input_loop()

    def nudge(self) -> None:
        """상태를 바꾼 쪽이 호출. 다음 프레임을 앞당긴다."""
        self._wake.set()

    # 렌더 ----------------------------------------------------
    def _render_loop(self) -> None:
        while not self._stop.is_set():
            # 이 try가 없으면 한 건의 실패에 렌더 스레드가 죽는다.
            # daemon=True라 프로그램은 살아 있고 화면만 그 순간에 멈춘다.
            # 사용자는 장이 조용한 줄 알게 된다 — 최악의 실패 방식이다.
            try:
                self._drain()
                self._drain_results()
                self._paint()
            except Exception:
                log.exception("렌더 실패, 다음 프레임에서 재시도")
            # 화면마다 다른 주기를 쓸 수 있다(Controller.render_interval).
            # 명령을 길게 타이핑하는 화면(수동 주문 등)은 기본 1초 주기로
            # 화면이 지워지면 입력 중에 지워져 버린다 — 그런 화면만 느리게
            # 그린다. nudge()는 그래도 즉시 깨우므로 명령 처리 후 반응은
            # 여전히 빠르다.
            interval = getattr(self.app.router.current, "render_interval", None)
            self._wake.wait(interval if interval is not None else self.interval)
            self._wake.clear()

    def _paint(self) -> None:
        width = shutil.get_terminal_size((100, 30)).columns
        ctrl = self.app.router.current
        print(frame(ctrl.title, ctrl.render(width), ctrl.hint(),
                             self.app.ctx.take_flash(), width), flush=True)

    def _drain(self, max_items: int = 5000) -> None:
        """큐는 흐름, 화면은 순간. 한 프레임에 쌓인 만큼을 접는다.

        큐에는 이미 파싱된 객체가 들어온다 — 여기서 파싱하지 않는다.
        모델 변경이 이 스레드 독점이라는 규칙 덕에 락이 필요 없다."""
        for _ in range(max_items):
            try:
                obj = self.view_q.get_nowait()
            except queue.Empty:
                return
            try:
                if obj is None:
                    continue
                # ① 대시보드 통계 — 타입 이름으로만 세므로 무엇이 와도 된다
                self.app.ctx.inbox.on_object(obj)
                # ② 구독형 화면 — 구독 안 된 타입은 알아서 흘려보낸다
                self.app.ctx.feed.on_object(obj)
            finally:
                self.view_q.task_done()

    def _drain_results(self) -> None:
        """워커가 끝낸 조회를 여기서 반영한다. 모델 변경은 이 스레드 독점."""
        while True:
            try:
                label, on_done, result, err = self._result_q.get_nowait()
            except queue.Empty:
                return
            if err is not None:
                log.error("%s 실패: %s", label, err, exc_info=err)
                self.app.ctx.flash(f"{label} 실패: {err}")
                continue
            if on_done is None:
                continue
            try:
                on_done(result)
            except Exception:
                log.exception("%s 콜백 실패", label)
                self.app.ctx.flash(f"{label} 처리 실패")

    def submit(self, label, fn, on_done=None) -> None:
        """fn은 워커 스레드에서, on_done은 렌더 스레드에서 실행된다."""
        def _run():
            try:
                result = fn()
            except Exception as e:
                self._result_q.put((label, on_done, None, e))
            else:
                self._result_q.put((label, on_done, result, None))
            finally:
                self.nudge()
        self._pool.submit(_run)

    # 입력 ----------------------------------------------------
    def _input_loop(self) -> None:
        while not self._stop.is_set():
            try:
                line = input().strip()
            except (EOFError, KeyboardInterrupt):
                self.quit()
                break
            if not line:
                self.nudge()
                continue
            key, _, arg = line.partition(" ")
            handler = self.app.router.resolve(key.lower())
            if handler is None:
                self.app.ctx.flash(f"알 수 없는 키: {key} ['?' 도움말]")
                self.nudge()
                continue
            # 여기서 블로킹 I/O를 부르면 응답이 올 때까지 입력이 멈춰,
            # 눌러도 반응 없는 것처럼 보인다. 핸들러는 submit만 한다.
            try:
                handler(self.app, arg.strip())
            except Exception:
                log.exception("키 처리 실패: %s", key)
                self.app.ctx.flash(f"'{key}' 처리 실패")
            self.nudge()
            if self._stop.is_set():
                break

    def quit(self) -> None:
        """렌더/입력 루프를 세우고, 엔진(웹소켓 등)도 같이 종료시킨다."""
        if self._stop.is_set():
            return
        self._stop.set()
        self._wake.set()
        print("\n종료합니다...", flush=True)
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self.on_quit is not None:
            try:
                self.on_quit()
            except Exception:
                log.exception("종료 콜백 실패")


class Application:
    """컨트롤러가 보는 얼굴이자, 화면 전체의 진입점.

    ■ Recorder 처럼 '큐에 put 만 하면 도는' 물건이다
        큐와 구독만 주면 컨트롤러 조립도, 스레드 기동도 스스로 한다.
        바깥에서 할 일은 두 가지뿐이다.

            app = Application(view_q=show_q, ws=ws, on_quit=ws.stop)
            app.subscribe(Tick, kis_model.Board(cols=["price"]), name="시세판")
            app.run()                      # 스레드로 띄우고 바로 반환

        컨트롤러 목록을 밖에서 만들어 넘기면, 화면을 하나 추가할 때마다
        호출부까지 고쳐야 한다. 여기서 만들면 그럴 일이 없다.

    ■ ctx 를 직접 만들 수도 있다
        기존처럼 ctx 를 조립해 넘기고 싶으면 ctx= 로 준다.
        안 주면 view_q / ws 로 알아서 만든다.
    """

    def __init__(self, view_q=None, ctx=None, ws=None, engine=None,
                 controllers=None, start="home", interval=1.0, workers=4,
                 on_quit=None):
        from alpha.view.controller import build_controllers
        from alpha.view.model import AppCtx

        self.view_q = view_q or queue.Queue()

        # engine 은 종종 Application 보다 늦게 만들어진다(view_q 를 먼저
        # 줘야 하므로) — 그럴 땐 None 으로 시작해서 나중에
        # app.ctx.engine = eng 로 채워도 된다. AppCtx 는 그냥 속성이다.
        self.ctx = ctx or AppCtx(ws=ws, view_q=self.view_q, engine=engine)
        # 컨트롤러를 안 주면 기본 세트를 만든다. 화면을 늘려도 호출부는 그대로.
        self.controllers = controllers or build_controllers(self.ctx)
        self.router = Router(self.controllers, start)
        self.runtime = Runtime(self, self.view_q, interval, workers, on_quit)
        self._thread: threading.Thread | None = None

    # ── 구독 (Recorder.subscribe 와 같은 자리) ──
    def subscribe(self, dtype, agg, name=None, where=None):
        """화면 하나를 늘린다. 등록 순서가 곧 숫자키다.

            app.subscribe(Tick, kis_model.Board(cols=["price"]), name="시세판")
            app.subscribe(Tick, kis_model.Recent(20), name="삼성",
                          where={"symbol": "005930"})
        """
        return self.ctx.feed.subscribe(dtype, agg, name=name, where=where)

    # ── 기동 ──
    def run(self, block: bool = False) -> None:
        """화면을 띄운다.

        block=False (기본) 면 스레드로 띄우고 바로 반환한다 —
        엔진·레코더와 나란히 올릴 때 쓴다.
        block=True 면 이 스레드에서 입력 루프를 돈다.
        """
        if block:
            self.runtime.start()
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._guarded_start,
                                        name="showing", daemon=True)
        self._thread.start()
        log.info("showing thread on.")

    def _guarded_start(self):
        """스레드에서 예외가 나면 조용히 죽는다. 로그를 남기고 종료 콜백을 부른다."""
        try:
            self.runtime.start()
        except Exception:
            log.exception("showing 스레드 종료")
            self.runtime.quit()

    # 기존 이름도 남긴다 (app.start() 로 부르던 코드 호환)
    start = run

    def goto(self, name, replace=True, **opts):
        self.router.goto(self, name, replace=replace, **opts)

    def back(self):
        self.router.back(self)

    def submit(self, label, fn, on_done=None):
        self.runtime.submit(label, fn, on_done)

    def quit(self):
        self.runtime.quit()

    def help_text(self) -> str:
        return self.router.help_text()