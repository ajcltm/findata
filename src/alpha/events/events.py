"""
═══════════════════════════════════════════════════════════════════
 events.py — 시장 이벤트 계층
═══════════════════════════════════════════════════════════════════

■ 풀려는 문제
    지금은 H0STCNT0(체결) / H0STASP0(호가) / H0STCNI0(체결통보) 세 종류지만,
    앞으로 예상체결, 프로그램매매, 투자자별 수급, 야간선물... 계속 늘어난다.
    피드가 하나 늘 때마다 Strategy 베이스를 고쳐야 한다면 설계가 잘못된 것이다.

■ 두 층으로 나눈다

    ┌ 원본(raw) ────────────────────────────────────────┐
    │  Execution(46필드) / OrderBook(59필드) / Notice    │  ← KIS 전용, 전부 str
    └───────────────────┬───────────────────────────────┘
                        │ normalize (이 파일)
    ┌ 정규화(core) ─────▼───────────────────────────────┐
    │  Tick / Quote / Bar — 작고, 숫자로 파싱됨, 안정적  │  ← 전략이 보는 것
    └───────────────────────────────────────────────────┘

    정규화 이벤트는 .raw 로 원본을 들고 있다. 특수 필드(체결강도, VI기준가)가
    필요한 전략은 거기서 꺼내 쓴다. 다만 그 순간 그 전략은 KIS 전용이 된다.

■ 왜 정규화가 필요한가 — 실제로 겪은 이유
    OrderBook 은 문서상 59필드인데 실제 전문은 62필드로 왔다.
    전략들이 원본 dataclass 를 직접 만졌다면 전략마다 다 깨진다.
    정규화 계층이 있으면 고칠 곳이 여기 한 곳이다.

    그리고 원본은 전부 str 이다. 전략이 int(exec.current_price) 를 하고 있으면
    안 된다. 파싱은 경계에서 한 번만.

■ 확장 방법
    새 피드가 생기면 ① 이벤트 dataclass 하나 추가, ② kind 문자열 정하기,
    ③ 그 피드를 쓰는 전략만 on_{kind} 훅 구현.
    Strategy 베이스도, 라우터도, 기존 전략도 건드리지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, time
from typing import Any, Optional, Protocol

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 1. 이벤트 기반 타입
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MarketEvent:
    """모든 시장 이벤트의 공통 뼈대.

    kind 가 디스패치 키다. 라우터가 on_{kind} 훅을 찾아 부른다.
        kind="tick"  → strategy.on_tick(ev)
        kind="quote" → strategy.on_quote(ev)
    새 피드는 kind 만 새로 정하면 된다. 라우터 코드는 그대로.
    """
    kind: str
    symbol: str
    dt: datetime                # ★ 파싱된 datetime. 문자열이 아니다
    raw: Any = None             # 원본 dataclass. 특수 필드가 필요할 때만 사용

    @property
    def variant(self):
        """같은 kind 안에서 더 세분해야 할 때 쓰는 키. 기본은 구분 없음.

        봉이 대표적이다. kind 는 둘 다 "bar" 지만 1분봉과 5분봉은
        다른 구독자에게 가야 한다. Bar 가 이 값을 seconds 로 덮어쓴다."""
        return None

    # ───────── 가격 질의 ─────────
    # 브로커가 if ev.kind == "tick" ... elif "bar" ... 로 분기하지 않게 하는 장치.
    # 이벤트가 자기 가격을 답하므로, 새 피드 종류가 생겨도 브로커는 그대로다.

    @property
    def ref_price(self) -> Optional[float]:
        """평가·수량계산의 기준가. 없으면 None."""
        return None

    @property
    def trade_price(self) -> Optional[float]:
        """실제 거래가 일어난 가격. 없으면 None.

        호가 갱신은 거래가 아니므로 None 이다 — 이 구분이 중요하다.
        지정가 체결 판정은 '거래가 났는가'로 하지 '호가가 닿았는가'로
        하지 않는다."""
        return None

    @property
    def quote(self) -> Optional[tuple[float, float]]:
        """(매수1호가, 매도1호가). 없으면 None."""
        return None

    @property
    def low_price(self) -> Optional[float]:
        """이 이벤트가 훑고 지나간 최저가. 틱이면 거래가 그 자체.
        봉이면 저가 — 종가만 보면 봉 안에서 지나간 가격을 놓친다."""
        return self.trade_price

    @property
    def high_price(self) -> Optional[float]:
        return self.trade_price


@dataclass(frozen=True)
class Tick(MarketEvent):
    """체결 하나. H0STCNT0 에서 정규화.

    ■ 왜 46필드 중 이것만 남기나
        전략이 실제로 쓰는 건 대부분 가격·수량이다. 나머지는 .raw 에 있다.
        정규화 타입을 작게 유지해야 다른 증권사·백테스트에서도 만들 수 있다.

    ■ bid/ask 가 여기 있는 이유
        H0STCNT0 은 체결가와 함께 1호가도 실어 보낸다. 즉 이 전문 하나로
        '지금 스프레드가 얼마인가'까지 알 수 있어서, 호가 전문(H0STASP0)을
        따로 구독하지 않아도 되는 전략이 많다.
    """
    price: float = 0.0
    volume: float = 0.0
    side: str = ""              # 체결구분: "buy"(매수체결) / "sell"(매도체결) / ""
    bid: Optional[float] = None # 매수1호가
    ask: Optional[float] = None # 매도1호가
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None

    @property
    def ref_price(self): return self.price

    @property
    def trade_price(self): return self.price

    @property
    def quote(self):
        return (self.bid, self.ask) if self.bid and self.ask else None

    @property
    def spread(self) -> Optional[float]:
        """호가 스프레드. 슬리피지 추정과 지정가 배치에 쓴다."""
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        """중간가. 스프레드가 넓을 때 체결가보다 공정한 기준가."""
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2
        return self.price


@dataclass(frozen=True)
class Quote(MarketEvent):
    """호가창 스냅샷. H0STASP0 에서 정규화.

    ■ 리스트로 담는 이유
        원본은 ask_price_1 ... ask_price_10 처럼 필드가 20개로 흩어져 있다.
        리스트면 for 문과 슬라이싱이 되고, 5호가짜리 다른 시장에도 쓸 수 있다.
            book.asks[0]        1호가
            sum(book.ask_sizes[:3])   3호가까지 잔량 합
    """
    asks: tuple[float, ...] = ()        # [0]이 1호가(최우선 매도)
    bids: tuple[float, ...] = ()
    ask_sizes: tuple[float, ...] = ()
    bid_sizes: tuple[float, ...] = ()
    total_ask_size: float = 0.0
    total_bid_size: float = 0.0

    @property
    def ref_price(self):
        """중간가. 호가만 오는 구간(장 시작 전 등)에도 기준가를 유지한다."""
        q = self.quote
        return (q[0] + q[1]) / 2 if q else None

    # trade_price 는 상속받은 None — 호가 갱신은 거래가 아니다

    @property
    def quote(self):
        b, a = self.best_bid, self.best_ask
        return (b, a) if b and a else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0] if self.asks else None

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0] if self.bids else None

    @property
    def spread(self) -> Optional[float]:
        if not (self.asks and self.bids):
            return None
        return self.asks[0] - self.bids[0]

    @property
    def imbalance(self) -> float:
        """호가 불균형. -1(매도 우위) ~ +1(매수 우위).

        계산: (총매수잔량 - 총매도잔량) / (둘의 합)
            매수 700, 매도 300 → (700-300)/1000 = +0.4  매수 압력
        단기 방향성 신호로 흔히 쓴다."""
        total = self.total_ask_size + self.total_bid_size
        if total <= 0:
            return 0.0
        return (self.total_bid_size - self.total_ask_size) / total


@dataclass(frozen=True)
class Bar(MarketEvent):
    """봉. 틱을 집계해서 만들거나, 과거 봉 API 에서 받는다.

    ★ 이제 Bar 는 특권적 타입이 아니다 ★
      Tick/Quote 와 나란한 이벤트 하나일 뿐이고,
      봉이 필요한 전략만 집계를 구독한다."""
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    seconds: int = 60           # 이 봉의 주기. 5분봉이면 300

    @property
    def variant(self):
        """주기가 라우팅 키다. 1분봉 구독자에게 5분봉이 가면
        지표가 다른 주기의 값으로 오염되어 조용히 틀린다."""
        return self.seconds

    @property
    def ref_price(self): return self.close

    @property
    def trade_price(self): return self.close

    @property
    def low_price(self): return self.low

    @property
    def high_price(self): return self.high


@dataclass(frozen=True)
class Notice(MarketEvent):
    """체결통보 하나(주문 접수/체결/거부 결과). H0STCNI0 또는 SimBroker의
    모의 체결에서 정규화.

    ■ 왜 필요한가
        실전(H0STCNI0, 대문자 필드)과 모의(SimBroker, 소문자 필드)가
        같은 정보를 필드 이름만 다르게 실어 보낸다. 하류(LiveRunner)가
        둘을 매번 hasattr 로 구분하는 대신, 소스 쪽(KiSEngine.
        _to_market_event / SimBroker._match)에서 여기로 한 번만
        정규화해두면 그 뒤로는 Tick/Quote 와 똑같이 다뤄진다 —
        구독(레코더·뷰)도 되고, 실전/모의 분기도 사라진다.
    """
    order_no: str = ""
    rejected: bool = False
    filled_qty: float = 0.0
    price: float = 0.0


# ═══════════════════════════════════════════════════════════════════
# 2. 정규화 — 원본 dataclass → 이벤트
# ═══════════════════════════════════════════════════════════════════
#
# ■ 원칙: 여기가 str → 숫자 변환의 유일한 지점이다.
#   전략 코드에 int()/float() 가 나오면 설계가 새고 있는 것.
#
# ■ 방어적으로 짠다. KIS 전문은 필드 수와 값 형식이 예고 없이 바뀐다.
#   (실제로 OrderBook 이 59 → 62필드로 관측됐다)
#   변환 실패는 None/0 으로 흡수하고, 전략을 죽이지 않는다.

def _f(v, default=0.0) -> float:
    """문자열 → float. 빈 값·공백·형식오류는 default.
    KIS 는 값이 없을 때 ""나 " "를 보낸다."""
    try:
        s = str(v).strip()
        return float(s) if s else default
    except (TypeError, ValueError):
        return default


def _fo(v) -> Optional[float]:
    """_f 와 같지만 값이 없으면 None. '0원'과 '값 없음'을 구별해야 할 때."""
    s = str(v).strip() if v is not None else ""
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_hhmmss(hhmmss: str, on: Optional[date] = None) -> datetime:
    """KIS 시각 문자열("093015") → datetime.

    ■ 왜 날짜를 따로 받나
        전문에는 시:분:초만 있다. 백테스트에서 과거 데이터를 재생할 때
        '오늘 날짜'를 붙이면 시각이 통째로 틀어진다.
        on=None 이면 오늘로 보되, 재생 시에는 반드시 넘길 것.
    """
    s = str(hhmmss).strip().zfill(6)
    on = on or date.today()
    try:
        return datetime.combine(on, time(int(s[0:2]), int(s[2:4]), int(s[4:6])))
    except ValueError:
        # 장운영 코드에 따라 "000000" 같은 값이 올 수 있다
        return datetime.combine(on, time(0, 0, 0))


# 체결구분 코드 → 방향. KIS 는 1=매도체결, 5=매수체결로 보낸다.
_EXEC_SIDE = {"1": "sell", "5": "buy"}


def from_execution(e, on: Optional[date] = None) -> Tick:
    """Execution(H0STCNT0, 46필드) → Tick.

    46개 중 8개만 꺼낸다. 나머지(체결강도, VI기준가, 누적거래대금...)는
    ev.raw.volume_power 처럼 필요할 때 꺼내 쓴다."""
    return Tick(
        kind="tick",
        symbol=e.stock_code,
        dt=parse_hhmmss(e.execution_time, on),
        raw=e,                                  # 원본 통째로 보관
        price=_f(e.current_price),
        volume=_f(e.tick_volume),
        side=_EXEC_SIDE.get(str(e.exec_division).strip(), ""),
        bid=_fo(e.bid_price_1),                 # 체결 전문에 1호가가 같이 온다
        ask=_fo(e.ask_price_1),
        bid_size=_fo(e.bid_rsvp_1),
        ask_size=_fo(e.ask_rsvp_1),
    )


def from_orderbook(b, depth: int = 10, on: Optional[date] = None) -> Quote:
    """OrderBook(H0STASP0) → Quote.

    ■ getattr 로 훑는 이유
        ask_price_1 ... ask_price_10 을 하나씩 쓰면 40줄이 된다.
        또 5호가만 오는 상품이 생겨도 depth 만 바꾸면 된다.
        필드가 없으면 조용히 건너뛴다 — 스키마가 바뀌어도 안 죽는다."""
    def series(prefix: str) -> tuple[float, ...]:
        out = []
        for i in range(1, depth + 1):
            v = getattr(b, f"{prefix}{i}", None)
            if v is None:
                break                           # 그 깊이까지 없으면 중단
            out.append(_f(v))
        return tuple(out)

    return Quote(
        kind="quote",
        symbol=b.stock_code,
        dt=parse_hhmmss(b.business_hour, on),
        raw=b,
        asks=series("ask_price_"),
        bids=series("bid_price_"),
        ask_sizes=series("ask_rsvp_"),
        bid_sizes=series("bid_rsvp_"),
        total_ask_size=_f(b.total_ask_rsvp),
        total_bid_size=_f(b.total_bid_rsvp),
    )


def from_notice(n, on: Optional[date] = None) -> Notice:
    """Notice(H0STCNI0, KIS 원문 대문자 필드) → Notice(정규화).

    ■ 모의(SimBroker)는 여기를 거치지 않는다
        SimBroker 는 자기 시계(self._now)를 이미 갖고 있어서, 체결
        판정 시점에 곧바로 Notice(정규화)를 만들어 큐에 넣는다 —
        이 함수는 실전 웹소켓 원문(대문자 필드)만 정규화한다.

    ★ CNTG_YN 을 반드시 봐야 한다 ★
        H0STCNI0 은 주문 하나에 최소 두 번 온다 — ① 거래소 접수
        (CNTG_YN="1": 주문/정정/취소/거부 접수) ② 실제 체결
        (CNTG_YN="2": 체결). 그런데 ①에서도 CNTG_QTY 에 주문수량이
        그대로 채워져 오는 경우가 있어서, CNTG_YN 을 안 보고 CNTG_QTY 만
        보면 '접수됐다'는 알림을 '전량 체결됐다'로 오인한다 — 지정가
        주문을 내자마자 FILLED 로 뜨고 취소가 막히는 사고가 여기서 난다.
        그래서 실제 체결(CNTG_YN=="2")일 때만 filled_qty 를 채운다."""
    filled = n.CNTG_YN == "2"
    return Notice(
        kind="notice",
        symbol=n.STCK_SHRN_ISCD,
        dt=parse_hhmmss(n.STCK_CNTG_HOUR, on),
        raw=n,
        order_no=n.ODER_NO,
        rejected=(n.RFUS_YN == "Y"),
        filled_qty=_f(n.CNTG_QTY) if filled else 0.0,
        price=_f(n.CNTG_UNPR),
    )


# ═══════════════════════════════════════════════════════════════════
# 3. 구독 — 어떤 전략이 어떤 (종목, 종류)를 받을지
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Subscription:
    """전략이 받고 싶은 이벤트 명세.

    symbol="*" 는 전 종목. 시장 전체를 스캔하는 전략용.
    variant 는 kind 안의 세부 구분 — 봉이면 주기(초).
        Subscription("005930", "bar", 60)    1분봉만
        Subscription("005930", "bar", 300)   5분봉만
        Subscription("005930", "tick")       틱 (구분 없음)
    """
    symbol: str
    kind: str
    variant: Any = None

    def matches(self, ev: MarketEvent) -> bool:
        return (self.kind == ev.kind
                and self.variant == ev.variant
                and (self.symbol == "*" or self.symbol == ev.symbol))


class Subscriber(Protocol):
    """라우터가 기대하는 구독자. 메서드 두 개면 된다.

    Trader 가 이걸 그대로 만족한다. 전략을 직접 등록하지 않는 이유는
    EventRouter.register 의 주석 참조."""

    def handles(self, kind: str) -> bool:
        """이 종류의 이벤트를 처리할 수 있나."""
        ...

    def dispatch(self, ev: "MarketEvent") -> None:
        """이벤트 하나 처리."""
        ...


class EventRouter:
    """이벤트를 구독자에게만 배달한다.

    ■ 구독자는 전략이 아니라 Trader 다
        전략의 on_bar 를 직접 부르면 Trader 가 하는 일이 전부 건너뛰어진다:
            워밍업 카운터 / 지표 갱신 / ready 확인 / 예외 격리 / dry-run 가드
        특히 워밍업이 빠지면 재시작할 때마다 과거 신호로 주문이 나간다.
        그래서 배달 경로에 Trader 가 반드시 끼어 있어야 한다.

    ■ 확장: 훅 이름 규약
        Trader.dispatch 가 getattr(strategy, f"on_{ev.kind}") 로 훅을 찾는다.
        새 피드 종류가 생겨도 이 라우터도 Trader 도 안 고친다.
        전략이 on_program_trade 를 정의하고 그 kind 를 구독하면 끝.

    ■ 성능
        (symbol, kind) → 구독자 리스트로 미리 인덱싱한다. 이벤트마다
        전 구독자를 순회하면 종목 200개 × 전략 10개에서 바로 느려진다.
    """

    def __init__(self):
        # (symbol, kind, variant) -> [subscriber, ...]
        #   와일드카드는 ("*", kind, variant) 로 저장
        #   variant 를 키에 넣어야 1분봉 구독자에게 5분봉이 안 간다
        self._routes: dict[tuple, list] = {}
        self._all: list = []

    def register(self, subscriber: Subscriber, subs: list[Subscription]):
        """구독자와 구독 목록을 등록한다.

        훅이 없으면 경고한다 — on_tick 을 on_tik 으로 오타내면 이벤트가
        조용히 안 오는데, 그게 제일 찾기 어려운 버그다."""
        self._all.append(subscriber)
        for s in subs:
            if not subscriber.handles(s.kind):
                log.warning("%s 가 '%s' 를 구독했지만 on_%s() 훅이 없다 — 오타?",
                            subscriber, s.kind, s.kind)
            self._routes.setdefault((s.symbol, s.kind, s.variant), []) \
                .append(subscriber)

    def targets(self, ev: MarketEvent) -> list:
        """이 이벤트를 받을 구독자들. 정확 매치 + 와일드카드.

        ev.variant 가 키에 포함되므로 1분봉 구독자는 1분봉만 받는다."""
        v = ev.variant
        return (self._routes.get((ev.symbol, ev.kind, v), [])
                + self._routes.get(("*", ev.kind, v), []))

    def dispatch(self, ev: MarketEvent):
        """배달. 예외 격리는 각 Trader 가 자기 안에서 한다."""
        for sub in self.targets(ev):
            sub.dispatch(ev)