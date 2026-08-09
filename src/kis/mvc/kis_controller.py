"""컨트롤러 — 입력을 받아 모델을 바꾸고, 필요하면 서비스를 부른다.

모델도 문자열도 만들지 않는다. 자기 모델과 자기 뷰가 뭔지만 안다.
핸들러 시그니처는 전부 (self, app, arg).
"""

from __future__ import annotations

from kis.mvc import kis_model, kis_view
from kis import kis_websocket


class Controller:
    name: str = ""
    title: str = ""
    view = None                        # (model, width) -> list[str]

    def __init__(self, m):
        self.model = m
        self.keymap: dict[str, callable] = {}

    def on_enter(self, app, **opts) -> None:
        """스택에 올라올 때. 무거운 조회는 반드시 app.submit으로."""

    def on_exit(self) -> None:
        """스택에서 내려갈 때."""

    def render(self, width: int) -> list[str]:
        return type(self).view(self.model, width)

    def hint(self) -> str:
        keys = list(self.keymap) + ["h", "r", "o", "b", "q"]
        return "  " + "  ".join(f"[{k}]" for k in keys)


class PagedKeys:
    """j/k/g. 계산은 모델이 한다."""

    def __init__(self, m):
        super().__init__(m)
        self.keymap.update({
            "j": lambda app, arg: self.model.down(),
            "k": lambda app, arg: self.model.up(),
            "g": lambda app, arg: self.model.top(),
        })


# ── 화면별 ─────────────────────────────────────────────────────
class HomeController(Controller):
    name, title = "home", "홈"
    view = staticmethod(kis_view.home)

    def __init__(self, ctx):
        super().__init__(kis_model.Home(ctx))
        self.keymap["s"] = self._subscribe

    def _subscribe(self, app, arg):
        """종목 구독"""
        if not arg:
            app.ctx.flash("사용법: s 005930")
            return
        app.submit(f"{arg} 구독",
                   lambda: app.ctx.ws._subscribe_one(kis_websocket.Subscription(app.ctx.ws.tr_price, arg)),
                   lambda _: app.ctx.flash(f"{arg} 구독 완료"))


class RealDataController(PagedKeys, Controller):
    name, title = "realdata", "실시간 시세"
    view = staticmethod(kis_view.realdata)

    def __init__(self, ctx):
        super().__init__(kis_model.RealData(ctx))
        self.keymap.update({"s": self._sort, "f": self._filter,
                            "d": self._detail})

    def _sort(self, app, arg):
        """정렬 전환"""
        self.model.next_sort()

    def _filter(self, app, arg):
        """필터 (f 005 / f 로 해제)"""
        self.model.only = arg or None
        self.model.top()

    def _detail(self, app, arg):
        """종목 상세 (d 005930)"""
        if not arg:
            app.ctx.flash("사용법: d 005930")
            return
        app.goto("detail", replace=False, code=arg)


class DetailController(Controller):
    name, title = "detail", "종목 상세"
    view = staticmethod(kis_view.detail)

    def __init__(self, ctx):
        super().__init__(kis_model.Detail(ctx))

    def on_enter(self, app, code=None, **opts):
        self.model.code = code
        self.title = f"종목 상세 {code}"

    def on_exit(self):
        self.model.code = None


class OrdersController(PagedKeys, Controller):
    name, title = "orders", "주문 내역"
    view = staticmethod(kis_view.orders)

    def __init__(self, ctx, order_api):
        super().__init__(kis_model.Orders(ctx))
        self.api = order_api                    # 서비스
        self.keymap["u"] = self._refresh

    def on_enter(self, app, account=None, **opts):
        self.model.account = account
        self.model.top()
        self._load(app)                 # 화면은 이미 떠 있고 조회만 뒤따른다

    def _refresh(self, app, arg):
        """새로고침"""
        self._load(app)

    def _load(self, app):
        m = self.model
        m.begin()

        def _run():
            try:
                return self.api.list_orders(m.account)
            except Exception as e:
                m.fail(str(e))          # ※ 워커 스레드 (기존 동작 유지)
                raise

        app.submit("주문 조회", _run, m.done)   # m.done은 렌더 스레드에서


def build_controllers(ctx, order_api) -> list[Controller]:
    return [
        HomeController(ctx),
        RealDataController(ctx),
        DetailController(ctx),
        OrdersController(ctx, order_api),
    ]