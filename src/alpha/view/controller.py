"""컨트롤러 — 키 입력을 받아 모델을 바꾼다.

■ 컨트롤러가 하는 일과 안 하는 일

    하는 일    키를 눌렀을 때 뭘 할지 정한다 (keymap)
               자기 모델이 뭔지, 자기 뷰가 뭔지 안다
               화면에 들어오고 나갈 때 준비/정리 (on_enter / on_exit)

    안 하는 일  데이터를 모으지 않는다          → 모델이 한다
               문자열을 만들지 않는다          → 뷰가 한다
               네트워크를 타지 않는다          → app.submit 으로 넘긴다

■ 화면 하나가 그려지는 순서

    사용자가 'r' 입력
        ↓
    Router.resolve("r")            어떤 함수를 부를지 찾는다
        ↓
    GLOBAL_KEYS["r"] → app.goto("realdata")
        ↓
    RealDataController.on_enter()  화면 준비
        ↓  (렌더 스레드가 1초마다)
    ctrl.render(width)
        ↓
    kis_view.realdata(model, width)  →  list[str]
        ↓
    kis_view.frame(...)            테두리 씌워서 출력

■ 핸들러 시그니처는 전부 (self, app, arg)

    입력이 "d 005930" 이면 key="d", arg="005930" 으로 갈라져서 온다.
    arg 가 없으면 빈 문자열이다.
"""

from __future__ import annotations

from alpha.view import model, view
from kis import kis_websocket


class Controller:
    """모든 화면의 부모.

    하위 클래스가 정할 것:
        name   화면 이름. app.goto("realdata") 의 그 이름이다
        title  화면 상단에 뜨는 제목
        view   (model, width) -> list[str] 함수. kis_view 의 것을 쓴다
    """

    name: str = ""
    title: str = ""
    view = None                     # (model, width) -> list[str]

    def __init__(self, m):
        self.model = m
        # 키 -> 함수.  {"s": self._sort, "f": self._filter}
        # 여기 없는 키는 Router.GLOBAL_KEYS 에서 찾는다.
        self.keymap: dict[str, callable] = {}

    def on_enter(self, app, **opts) -> None:
        """이 화면으로 들어올 때 1회.

        ★ 여기서 네트워크를 타면 안 된다 ★
          렌더 스레드가 멈춰서 화면이 얼어붙는다.
          무거운 조회는 반드시 app.submit(...) 으로 넘긴다."""

    def on_exit(self) -> None:
        """이 화면에서 나갈 때 1회. 임시 상태를 지우는 자리."""

    def render(self, width: int) -> list[str]:
        """화면 본문을 문자열 목록으로. 렌더 스레드가 1초마다 부른다.

        type(self).view 로 부르는 이유:
          view 는 staticmethod 라서 self.view 로 부르면 model 이
          첫 인자로 안 들어간다."""
        return type(self).view(self.model, width)

    def hint(self) -> str:
        """화면 맨 아래에 쓸 키 목록.  "  [j] [k] [h] [q]" 같은 것."""
        keys = list(self.keymap) + ["h", "r", "o", "v", "b", "q"]
        return "  " + " ".join(f"[{k}]" for k in keys)


class PagedKeys:
    """스크롤 키(j/k/g)를 붙여주는 조각.

    ■ 쓰는 법
        class 무엇Controller(PagedKeys, Controller):   ← PagedKeys 를 앞에
            ...
        모델은 Paged 를 상속해야 한다 (down/up/top 을 갖고 있어야 하므로).

    ■ 왜 앞에 쓰나
        파이썬은 왼쪽부터 찾는다. PagedKeys.__init__ 이 먼저 돌면서
        super().__init__(m) 으로 Controller.__init__ 을 부르고,
        그다음 keymap 에 j/k/g 를 얹는다. 순서를 바꾸면 keymap 이
        아직 없는 상태에서 update 하려다 터진다.

    ■ 계산은 모델이 한다
        여기서는 model.down() 을 부르기만 한다. 몇 줄 내릴지,
        끝에서 더 내려가지 않게 막는 건 Paged 의 일이다.
    """

    def __init__(self, m):
        super().__init__(m)
        self.keymap.update({
            "j": lambda app, arg: self.model.down(),   # 다음 페이지
            "k": lambda app, arg: self.model.up(),     # 이전 페이지
            "g": lambda app, arg: self.model.top(),    # 맨 위로
        })


# ══════════════════════════════════════════════════════════════════
# 화면별 컨트롤러
# ══════════════════════════════════════════════════════════════════

class HomeController(Controller):
    """홈 — 대시보드.

    무엇이 얼마나 들어오고 있는지, 구독 화면이 뭐가 있는지 보여준다.
    키는 종목 구독(s) 하나뿐이다."""

    name, title = "home", "홈"
    view = staticmethod(view.home)

    def __init__(self, ctx):
        super().__init__(model.Home(ctx))
        self.keymap["s"] = self._subscribe

    def _subscribe(self, app, arg):
        """종목 구독"""
        # arg 는 "005930" 처럼 키 뒤에 붙여 입력한 값이다.
        if not arg:
            app.ctx.flash("사용법: s 005930")
            return
        if app.ctx.ws is None:
            app.ctx.flash("웹소켓이 연결되지 않았습니다.")
            return

        # ★ 여기서 직접 부르면 입력이 멈춘다 ★
        #   구독 요청은 네트워크를 탄다. app.submit 이 워커 스레드에서
        #   돌리고, 끝나면 세 번째 인자(콜백)를 렌더 스레드에서 부른다.
        app.submit(
            f"{arg} 구독",                                      # 로그용 이름
            lambda: app.ctx.ws._subscribe_one(                  # 워커에서 실행
                kis_websocket.Subscription(app.ctx.ws.tr_price, arg)),
            lambda _: app.ctx.flash(f"{arg} 구독 완료"))        # 렌더에서 실행


class RealDataController(PagedKeys, Controller):
    """수신 로그 — 큐에 들어온 것을 시간순으로 훑는다.

    종목별 시세판이 보고 싶으면 구독 화면(v)의 Board 를 쓴다.
    이쪽은 '무엇이 흐르고 있나' 를 날것으로 보는 용도다."""

    name, title = "realdata", "수신 로그"
    view = staticmethod(view.realdata)

    def __init__(self, ctx):
        super().__init__(model.RealData(ctx))
        self.keymap.update({"f": self._filter, "d": self._detail})

    def _filter(self, app, arg):
        """필터 (f 005930 / f 로 해제)"""
        # arg 가 빈 문자열이면 None 을 넣어 필터를 끈다
        self.model.only = arg or None
        self.model.top()            # 필터가 바뀌면 맨 위로

    def _detail(self, app, arg):
        """종목 상세 (d 005930)"""
        if not arg:
            app.ctx.flash("사용법: d 005930")
            return
        # replace=False 면 지금 화면 위에 쌓인다.
        # 'b'(뒤로)를 누르면 여기로 돌아온다.
        app.goto("detail", replace=False, code=arg)


class DetailController(Controller):
    """종목 상세 — 한 종목만 걸러서 본다.

    데이터를 따로 안 쌓는다. 구독 중인 Board 패널에서 그 종목 줄만
    뽑아 온다 — 같은 데이터를 두 벌 쌓을 이유가 없다."""

    name, title = "detail", "종목 상세"
    view = staticmethod(view.detail)

    def __init__(self, ctx):
        super().__init__(model.Detail(ctx))

    def on_enter(self, app, code=None, **opts):
        # goto("detail", code="005930") 의 code 가 여기로 들어온다
        self.model.code = code
        self.title = f"종목 상세 {code}"

    def on_exit(self):
        # 나갈 때 지운다. 안 지우면 다음에 들어올 때 이전 종목이 남는다.
        self.model.code = None


class OrdersController(PagedKeys, Controller):
    """주문 내역 — 증권사 API 로 조회한다.

    ■ 조회가 세 단계로 나뉘는 이유
        네트워크는 느리다. 그동안 화면이 멈추면 안 되므로
        '조회 중' 을 먼저 그리고, 결과가 오면 그때 채운다.

            begin()  화면에 "조회 중..." 이 뜬다
            (워커 스레드에서 API 호출)
            done()   결과를 모델에 넣는다 — 렌더 스레드에서
    """

    name, title = "orders", "주문 내역"
    view = staticmethod(view.orders)

    def __init__(self, ctx, order_api):
        super().__init__(model.Orders(ctx))
        self.api = order_api            # 조회 서비스. 없으면 None
        self.keymap["u"] = self._refresh

    def on_enter(self, app, account=None, **opts):
        self.model.account = account
        self.model.top()
        self._load(app)                 # 화면은 이미 떠 있고 조회만 뒤따른다

    def _refresh(self, app, arg):
        """새로고침"""
        self._load(app)

    def _load(self, app):
        if self.api is None:
            self.model.fail("주문 API가 연결되지 않았습니다.")
            return

        m = self.model
        m.begin()                       # 화면에 "조회 중..." 이 뜬다

        def call_api():
            """워커 스레드에서 실행된다. 여기서 모델을 만지면 안 된다."""
            return self.api.list_orders(m.account)

        # 세 인자: 로그용 이름 / 워커에서 돌릴 함수 / 렌더에서 돌릴 콜백
        # 예외가 나면 submit 이 잡아서 flash 로 띄운다.
        app.submit("주문 조회", call_api, m.done)


class FeedController(PagedKeys, Controller):
    """구독 화면 — 숫자키로 패널을 고른다.

    ■ 다른 컨트롤러와 구조가 똑같다
        모델·뷰·키맵. 다만 이 하나가 '범용' 이라 데이터 종류가 늘어도
        컨트롤러를 새로 만들 필요가 없다.
        app.subscribe(...) 한 줄이면 숫자키가 하나 는다.

    ■ 숫자키가 어떻게 붙나
        1~9 를 미리 등록해 둔다. 구독이 3개뿐이면 4~9 를 눌러도
        "4번 화면이 없습니다" 만 뜨고 아무 일도 안 일어난다.
    """

    name, title = "feed", "구독 화면"
    view = staticmethod(view.feed)

    def __init__(self, ctx):
        super().__init__(model.Feed(ctx))
        for number in range(1, 10):
            # str(1) → "1" 키에, 패널 번호 0 을 고르는 함수를 붙인다.
            self.keymap[str(number)] = self._make_selector(number - 1)

    def _make_selector(self, index: int):
        """index 번 패널을 고르는 함수를 만들어 돌려준다.

        ★ 왜 함수를 만들어서 돌려주나 ★
          for 문 안에서 곧바로
              self.keymap[str(n)] = lambda app, arg: self.model.select(n - 1)
          라고 쓰면 안 된다. 람다는 n 의 '값' 이 아니라 '변수' 를 기억해서,
          for 가 끝난 뒤 모든 람다가 마지막 n(=9)을 보게 된다.
          별도 함수로 감싸면 index 가 그 함수 안에 갇혀서 안전하다.
        """
        def handler(app, arg):
            """패널 선택"""
            if self.model.select(index):
                # 제목에 지금 보는 패널 이름을 붙인다
                self.title = f"구독 화면 · {self.model.panel.name}"
            else:
                app.ctx.flash(f"{index + 1}번 화면이 없습니다.")
        return handler

    def on_enter(self, app, **opts):
        panel = self.model.panel
        if panel is None:
            self.title = "구독 화면"
        else:
            self.title = f"구독 화면 · {panel.name}"


def build_controllers(ctx, order_api=None) -> list[Controller]:
    """화면 목록을 만든다. Application 이 알아서 부른다.

    화면을 하나 더 만들었다면 여기에 추가하면 된다.
    다만 구독 화면(feed)으로 충분한 경우가 대부분이다 —
    그쪽은 app.subscribe() 한 줄이면 되므로 여기를 안 고쳐도 된다.

    맨 앞이 시작 화면이 아니다. 시작 화면은
    Application(start="home") 으로 정한다."""
    return [
        HomeController(ctx),
        RealDataController(ctx),
        DetailController(ctx),
        OrdersController(ctx, order_api),
        FeedController(ctx),
    ]