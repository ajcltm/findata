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

import shlex

from alpha.view import model, view
from alpha.strategy.manual import MANUAL_STRATEGY_ID
from alpha.trader.trading import OrderStatus

# 전역 키 짧은 설명. app.py 의 Router.GLOBAL_KEYS 와 짝이 맞아야 한다.
GLOBAL_KEY_HINTS = {
    "h": "홈", "r": "시세", "o": "주문", "v": "구독", "b": "뒤로", "q": "종료",
}


class Controller:
    """모든 화면의 부모.

    하위 클래스가 정할 것:
        name   화면 이름. app.goto("realdata") 의 그 이름이다
        title  화면 상단에 뜨는 제목
        view   (model, width) -> list[str] 함수. kis_view 의 것을 쓴다

    render_interval : None 이면 Runtime 의 기본 주기(보통 1초)를 그대로
        쓴다. 명령을 길게 타이핑해야 하는 화면(수동 주문 등)은 그 주기로
        화면이 지워지면 입력 중에 지워져 버리므로, 그런 화면만 값을 늘려
        오버라이드한다. nudge() 는 그래도 즉시 깨우므로 키 처리 자체는
        느려지지 않는다."""

    name: str = ""
    title: str = ""
    view = None                     # (model, width) -> list[str]
    render_interval: float | None = None

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
        """화면 맨 아래에 쓸 키 목록.  "  [j:다음] [k:이전] [h:홈]" 같은 것.

        각 키의 설명은 그 핸들러의 docstring 첫 줄이다 — 핸들러를 추가할
        때 짧은 한 줄 docstring만 붙이면 힌트가 저절로 따라온다. 전역 키와
        같은 글자를 화면이 자기 keymap 에 넣어 가리는 경우(예: 주문 화면의
        'o') 화면 쪽 설명이 우선하고 중복으로 안 뜬다."""
        return self._render_hint(self.keymap)

    def _render_hint(self, keys: dict) -> str:
        """keys(보통 self.keymap)로 힌트 문자열을 만든다.

        FeedController 처럼 keymap 일부(패널 번호 등)를 힌트에서 빼고
        싶은 화면이 오버라이드해서 걸러낸 dict 를 넘길 수 있게 별도
        메서드로 뺐다."""
        parts = []
        for k, fn in keys.items():
            doc = (getattr(fn, "__doc__", None) or "").strip()
            label = doc.splitlines()[0] if doc else None
            parts.append(f"[{k}:{label}]" if label else f"[{k}]")
        for k, label in GLOBAL_KEY_HINTS.items():
            if k not in keys:
                parts.append(f"[{k}:{label}]")
        return "  " + " ".join(parts)


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
        self.keymap.update({"j": self._down, "k": self._up, "g": self._top})

    # 짧은 docstring이 힌트 줄(예: [j:다음])에 그대로 쓰인다 — 람다는
    # __doc__ 이 없어서 이 목적으로는 이름 붙은 메서드가 필요하다.
    def _down(self, app, arg):
        """다음"""
        self.model.down()

    def _up(self, app, arg):
        """이전"""
        self.model.up()

    def _top(self, app, arg):
        """맨위"""
        self.model.top()


# ══════════════════════════════════════════════════════════════════
# 화면별 컨트롤러
# ══════════════════════════════════════════════════════════════════

class HomeController(Controller):
    """홈 — 대시보드.

    무엇이 얼마나 들어오고 있는지, 구독 화면이 뭐가 있는지 보여준다.
    키는 종목 구독(s)/구독 해제(sc) 둘이다."""

    name, title = "home", "홈"
    view = staticmethod(view.home)

    def __init__(self, ctx):
        super().__init__(model.Home(ctx))
        self.keymap["s"] = self._subscribe
        self.keymap["sc"] = self._unsubscribe

    def _universe(self, app):
        """엔진에 유니버스 매니저가 꽂혀 있으면 그걸 돌려주고, 없으면 None.

        ★ 왜 있으면 반드시 이걸 거쳐야 하나 ★
          ws.subscribe_symbol() 을 콘솔에서 직접 부르면 시세만 들어오고
          그 종목을 처리할 Strategy/지표가 없다(반쪽짜리 구독). 반대로
          ws.unsubscribe_symbol() 을 직접 부르면 유니버스는 그 종목이
          여전히 활성인 줄 알고 있는데 시세만 끊겨 지표가 조용히 멈춘다.
          유니버스가 있으면 request_add/remove() 가 ws 구독과
          Engine.add()/remove() 를 한 자리에서 같이 처리해준다."""
        eng = app.ctx.engine
        return getattr(eng, "universe", None) if eng is not None else None

    def _subscribe(self, app, arg):
        """종목 구독"""
        # arg 는 "005930" 처럼 키 뒤에 붙여 입력한 값이다.
        if not arg:
            app.ctx.flash("사용법: s 005930")
            return
        if app.ctx.ws is None:
            app.ctx.flash("웹소켓이 연결되지 않았습니다.")
            return

        universe = self._universe(app)
        if universe is not None:
            # request_add() 는 큐에 넣기만 하고 바로 반환한다(스레드 안전) —
            # 실제 ws 구독 + Engine.add() 는 다음 유니버스 재계산 때(대략
            # 1초 이내) LiveRunner 스레드에서 실행된다. desired_fn() 이
            # 이 종목을 계속 안 돌려주면 다음 정기 재계산 때 다시 빠질 수
            # 있다는 점은 알려준다.
            universe.request_add(arg)
            app.ctx.flash(f"{arg} 유니버스 추가 요청 — 곧 전략과 함께 반영됩니다 "
                          f"(desired_fn 이 계속 이 종목을 안 돌려주면 다음 정기 재계산 때 다시 빠질 수 있음)")
            return

        # ★ 유니버스가 없을 때만 ws를 직접 건드린다 ★
        #   이 경로는 시세만 구독되고 어떤 전략/지표도 그 종목을 보지
        #   않는다 — build_universe() 없이 옛 방식으로 돌리는 경우의
        #   대체 수단일 뿐이다.
        #
        # ★ 여기서 직접 부르면 입력이 멈춘다 ★
        #   구독 요청은 네트워크를 탄다. app.submit 이 워커 스레드에서
        #   돌리고, 끝나면 세 번째 인자(콜백)를 렌더 스레드에서 부른다.
        app.submit(
            f"{arg} 구독",                                      # 로그용 이름
            lambda: app.ctx.ws.subscribe_symbol(arg),           # 워커에서 실행
            lambda ok: app.ctx.flash(
                (f"{arg} 시세만 구독 완료" if ok else f"{arg} 목록에 추가됨(다음 재연결 때 반영)")
                + " — 유니버스가 없어 전략/지표는 안 붙습니다"))

    def _unsubscribe(self, app, arg):
        """종목 구독 해제"""
        if not arg:
            app.ctx.flash("사용법: sc 005930")
            return
        if app.ctx.ws is None:
            app.ctx.flash("웹소켓이 연결되지 않았습니다.")
            return

        universe = self._universe(app)
        if universe is not None:
            # 포지션이 남아있으면 유니버스가 즉시는 못 떼고 청산 대기로
            # 돌린다(신규진입만 차단, 시세·지표는 유지) — rebalance() 와
            # 완전히 같은 규칙이라 콘솔에서 강제로 위험하게 끊을 수 없다.
            universe.request_remove(arg)
            app.ctx.flash(f"{arg} 유니버스 제거 요청 — 곧 반영됩니다 "
                          f"(포지션이 남아있으면 청산될 때까지 시세는 유지됩니다)")
            return

        # KIS 실시간 시세는 tr_type="2"로 같은 tr_id/tr_key를 다시 보내면
        # 해제된다(공식 문서 H0STCNT0 설명 참고) — 유니버스가 없을 때만
        # 쓰는 대체 수단이라, 이 경로는 포지션 여부를 전혀 모른다.
        app.submit(
            f"{arg} 구독해제",
            lambda: app.ctx.ws.unsubscribe_symbol(arg),
            lambda ok: app.ctx.flash(
                (f"{arg} 시세 구독 해제 완료" if ok else f"{arg} 목록에서 제거됨(연결이 없어 즉시 반영은 안 됨)")
                + " — 유니버스가 없어 포지션 확인 없이 바로 끊었습니다"))


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
        """필터

        f 005930 / f 로 해제"""
        # arg 가 빈 문자열이면 None 을 넣어 필터를 끈다
        self.model.only = arg or None
        self.model.top()            # 필터가 바뀌면 맨 위로

    def _detail(self, app, arg):
        """종목상세

        d 005930"""
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


def _parse_flags(arg: str) -> dict[str, str]:
    """"-s 1 -q 10 -p 70000 -d buy" → {"s":"1","q":"10","p":"70000","d":"buy"}.

    "-"로 시작하는 토큰을 키로, 그 다음 토큰을 값으로 묶는다.
    shlex 를 쓰는 이유는 그냥 str.split() 보다 견고해서다(따옴표 등)."""
    tokens = shlex.split(arg)
    out: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-") and len(tok) > 1 and i + 1 < len(tokens):
            out[tok.lstrip("-")] = tokens[i + 1]
            i += 2
        else:
            i += 1
    return out


class OrderEntryController(Controller):
    """수동 주문 — 구독 종목 번호 목록을 보여주고, 콘솔 명령으로 broker 에
    직접 실제 주문을 넣는다.

    ■ 'o'는 화면 진입, 'ob'/'os'가 실제 주문이다
        'o'(전역 키, Router.GLOBAL_KEYS)는 이 화면으로 이동만 한다.
        매수/매도는 로컬 키맵의 'ob'/'os'로 낸다 — 방향을 -d 플래그로
        따로 받지 않고 키 자체가 방향이다. 나머지 플래그(-s/-q/-p)는
        순서에 상관없이 아무렇게나 섞어 써도 된다(_parse_flags 가
        "-플래그 값" 쌍으로 걷어내지, 위치로 읽지 않는다).

            o                              화면 진입/새로고침
            ob -s 1 -q 10                  1번 종목 10주 매수(시장가)
            os -s 005930 -q 10 -p 70000    구독 안 한 종목도 지정가 매도
            ob -q 10 -s 1 -p 70000         순서를 바꿔도 동일하게 해석됨

    ■ -s 가 번호인지 종목코드인지
        국내 종목코드는 항상 6자리라, 6자리가 아닌 숫자만 번호(목록의
        몇 번째)로 본다. 그래서 "005930"처럼 실제 코드를 그대로 써도
        번호로 오인되지 않고, 구독하지 않은 종목도 코드로 주문할 수 있다.

    ■ 왜 여기서 네트워크를 직접 안 부르나
        broker.buy()/sell() 이 실브로커(KISBroker)까지 내려가면 REST
        호출이 될 수 있다. 입력 스레드에서 그대로 부르면 응답이 올 때까지
        키 입력이 멈춘다 — 반드시 app.submit 으로 워커에 넘긴다."""

    name, title = "orders", "수동 주문"
    view = staticmethod(view.order_entry)
    # 명령이 길다(-s -q -p) — 기본 1초 주기로 화면이 지워지면 타이핑
    # 도중에 지워진다. 다 쓸 때까지 여유를 주고, 결과 확인 후 반응은
    # nudge()가 여전히 즉시 처리한다.
    render_interval = 60.0

    def __init__(self, ctx):
        super().__init__(model.OrderEntry(ctx))
        self.keymap["ob"] = self._buy
        self.keymap["os"] = self._sell

    # 짧은 docstring이 힌트 줄(예: [ob:매수])에 그대로 쓰인다 — 람다는
    # __doc__ 이 없어서 이 목적으로는 이름 붙은 메서드가 필요하다.
    # 실제 처리는 둘 다 _submit() 으로 모은다.
    def _buy(self, app, arg):
        """매수"""
        self._submit(app, arg, "buy")

    def _sell(self, app, arg):
        """매도"""
        self._submit(app, arg, "sell")

    def _submit(self, app, arg, side: str):
        """ob|os -s 번호|종목코드 -q 수량 [-p 가격], p 생략 시 시장가"""
        if not arg:
            app.ctx.flash("사용법: ob|os -s 번호|종목코드 -q 수량 [-p 가격]")
            return

        try:
            symbol, qty, price = self._parse_order(arg)
        except ValueError as e:
            app.ctx.flash(str(e))
            return

        broker = self._manual_broker(app)
        if broker is None:
            app.ctx.flash("수동 주문 전략이 연결되지 않았습니다.")
            return

        def place():
            """워커 스레드에서 실행된다. 실전이면 여기서 REST 호출이 나간다."""
            fn = broker.buy if side == "buy" else broker.sell
            return fn(symbol, qty, price=price, tag="manual")

        def done(order):
            """렌더 스레드에서 실행된다. 모델은 여기서만 만진다.

            ★ 여기서 order 를 view_q 에 직접 넣는 이유 ★
              Engine.feed_order() 는 체결통보(Notice)가 와서 주문 상태가
              바뀔 때만 불린다 — 방금 낸 주문은 아직 그런 통보를 못
              받았다. 지정가 주문은 체결될 때까지(또는 영영 안 될 수도
              있다) feed_order() 가 단 한 번도 안 불려서, 여기서 안 넣으면
              'v' 화면의 주문 Board 에 영원히 안 보인다. 나중에 체결/거부
              통보가 오면 Engine.feed_order() 가 같은 order.id 로 그 줄을
              최신 상태로 갱신한다."""
            if order is not None:
                app.view_q.put(order)
            self.model.last_result = self._describe(symbol, side, qty, order)
            app.ctx.flash(self.model.last_result)

        app.submit(f"{symbol} {side} 주문", place, done)

    # ── 명령 해석 ────────────────────────────────────────────
    def _parse_order(self, arg: str) -> tuple[str, float, "float | None"]:
        opts = _parse_flags(arg)

        raw_symbol = opts.get("s")
        if not raw_symbol:
            raise ValueError("사용법: ob|os -s 번호|종목코드 -q 수량 [-p 가격]")
        symbol = self._resolve_symbol(raw_symbol)
        if symbol is None:
            raise ValueError(f"{raw_symbol}: 해당 번호의 종목이 없습니다.")

        try:
            qty = int(opts["q"])
        except (KeyError, ValueError):
            raise ValueError("-q 수량(숫자)이 필요합니다.")
        if qty <= 0:
            raise ValueError("-q 수량은 0보다 커야 합니다.")

        price = None
        if "p" in opts:
            try:
                price = float(opts["p"])
            except ValueError:
                raise ValueError("-p 가격이 숫자가 아닙니다.")

        return symbol, qty, price

    def _resolve_symbol(self, s: str) -> str | None:
        """번호(목록 인덱스, 1부터) 또는 종목코드 그대로.

        종목코드는 항상 6자리라, 6자리가 아닌 숫자 문자열만 번호로 본다
        — "005930"이 번호 5930으로 오인되는 일이 없다."""
        symbols = self.model.symbols()
        if s.isdigit() and len(s) != 6:
            idx = int(s) - 1
            return symbols[idx] if 0 <= idx < len(symbols) else None
        return s                # 종목코드 그대로 — 구독 여부와 무관하게 허용

    def _manual_broker(self, app):
        """수동 주문 전략(alpha.strategy.manual.MANUAL_STRATEGY_ID)의
        StrategyBroker. engine 이 아직 안 붙었으면(연결 전/백테스트) None."""
        engine = app.ctx.engine
        if engine is None:
            return None
        slot = engine.slots.get(MANUAL_STRATEGY_ID)
        return slot.view if slot else None

    def _describe(self, symbol: str, side: str, qty: float, order) -> str:
        if order is None:
            return f"{symbol} {side} 주문 무시됨 (미체결 주문 존재 또는 최소수량 미만)"
        if order.status is OrderStatus.REJECTED:
            return f"{symbol} {side} 주문 거부: {order.reject_reason}"
        kind = f"{order.price:,.0f}원 지정가" if order.price else "시장가"
        return f"{symbol} {side} {qty:g}주 {kind} 주문 접수 (id={order.id})"


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

    @property
    def render_interval(self) -> float | None:
        """지금 보고 있는 패널이 정한 주기를 그대로 쓴다.

        ★ 이 화면 전체를 한 값으로 고정하면 안 된다 ★
          FeedController 하나가 시세판/호가/봉/포지션/주문 등 서로 다른
          성격의 패널을 전부 담고 있다. 'oc 주문id' 타이핑 중에 화면이
          지워지지 않아야 하는 건 주문 패널뿐인데, 예전엔 여기를 60.0
          으로 고정해서 다른 실시간 패널(시세판 등)의 자동 갱신까지
          전부 60초로 같이 느려졌었다.

          이제 패널마다 add_view(..., render_interval=...)로 원하는 값을
          주면(Panel.render_interval) 그 패널을 보는 동안만 그 주기를
          쓰고, 안 준 패널은 None 이라 Runtime 기본값(1초)이 그대로
          적용된다. 패널 전환·oc 처리 직후 반응은 nudge()가 항상 즉시
          처리하므로, 느려지는 건 '아무 키도 안 눌렀을 때의 대기 화면
          자동 새로고침'뿐이다 — 주문 패널을 60초로 줘도 안전한 이유."""
        panel = self.model.panel
        return panel.render_interval if panel is not None else None

    def __init__(self, ctx):
        super().__init__(model.Feed(ctx))
        for number in range(1, 20):
            # str(1) → "1" 키에, 패널 번호 0 을 고르는 함수를 붙인다.
            self.keymap[str(number)] = self._make_selector(number - 1)
        self.keymap["oc"] = self._cancel_order

    def _cancel_order(self, app, arg):
        """주문취소

        oc 주문id (예: oc o000005). 어느 패널을 보고 있는지와 무관하게
        동작한다 — id 하나로 이미 어떤 주문인지 정해지므로 '주문' 패널을
        먼저 골라야 할 이유가 없다."""
        order_id = arg.strip()
        if not order_id:
            app.ctx.flash("사용법: oc 주문id (예: oc o000005)")
            return

        engine = app.ctx.engine
        if engine is None:
            app.ctx.flash("엔진이 연결되지 않았습니다.")
            return

        # PortfolioBroker.owner_of() 로 이 주문을 낸 전략을 찾고,
        # 그 전략의 StrategyBroker(_orders)에서 실제 Order 객체를 꺼낸다 —
        # 수동 주문이든 자동 전략 주문이든 같은 방식으로 찾아진다.
        sid = engine.portfolio.owner_of(order_id)
        slot = engine.slots.get(sid) if sid else None
        order = slot.view._orders.get(order_id) if slot else None
        if order is None:
            app.ctx.flash(f"{order_id}: 주문을 찾을 수 없습니다.")
            return
        if order.is_done:
            app.ctx.flash(f"{order_id}: 이미 종료된 주문입니다 ({order.status.value}).")
            return

        broker = slot.view

        def cancel_it():
            """워커 스레드에서 실행된다. 실전이면 여기서 REST 취소 요청이
            나간다(KISBroker.cancel) — 입력 스레드에서 직접 부르면 안 된다."""
            broker.cancel(order)
            return order

        def done(order):
            """렌더 스레드에서 실행된다.

            취소 '확정'은 나중에 체결통보로 온다(Engine.feed_execution →
            feed_order 가 그때 CANCELED 로 갱신된 order 를 다시 흘린다).
            여기서는 방금 요청을 보냈다는 것만 즉시 반영한다 — 안 넣으면
            (SimBroker 는 즉시 CANCELED 로 바뀌는데도) 확정 통보가 없는
            실전 경로처럼 화면에 아무 변화가 없어 보인다."""
            app.view_q.put(order)
            app.ctx.flash(f"{order_id} 취소 요청 (현재상태: {order.status.value})")

        app.submit(f"{order_id} 취소", cancel_it, done)

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

    def hint(self) -> str:
        """패널 번호(1~19)는 화면 위 menu() 에 이미 다 나와 있다("[1]시세판*
        [2]호가 ..." 처럼) — 아래 힌트 줄에 또 줄줄이 늘어놓으면 중복이고
        한 줄만 길어진다. 번호 키만 빼고 나머지(oc, 전역 키)는 그대로 보여준다."""
        keys = {k: fn for k, fn in self.keymap.items() if not k.isdigit()}
        return self._render_hint(keys)


def build_controllers(ctx) -> list[Controller]:
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
        OrderEntryController(ctx),
        FeedController(ctx),
    ]