"""
═══════════════════════════════════════════════════════════════════
 normalize.py — 정규화. 원본(raw) dataclass → 이벤트(Tick/Quote/Notice)
═══════════════════════════════════════════════════════════════════

옛 위치: alpha/events/events.py 의 "2. 정규화" 절.
이벤트 타입(MarketEvent/Tick/Quote/Bar/Notice)은 events.py 에 남는다.
"""

from __future__ import annotations

from datetime import datetime, date, time
from typing import Optional

from alpha.events.events import Notice, Quote, Tick


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


def _parse_recv(iso: str) -> Optional[datetime]:
    """KISParser.now() 가 찍은 ISO 문자열("...T09:30:15.123456") → datetime.
    비어있거나 형식이 이상하면 None — recv_dt 가 없다는 뜻으로 그대로 둔다
    (dt 는 항상 필요해서 실패 시 00:00:00 으로라도 채우지만, recv_dt 는
    없으면 그냥 없는 게 맞다 — 잘못된 값을 있는 것처럼 꾸미지 않는다)."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
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


# 체결구분 코드 → 방향. KIS 는 1:매수(+), 5:매도(-) 3:장전
_EXEC_SIDE = {"1": "buy", "5": "sell"}


def from_execution(e, on: Optional[date] = None) -> Tick:
    """Execution(H0STCNT0, 46필드) → Tick.

    46개 중 8개만 꺼낸다. 나머지(체결강도, VI기준가, 누적거래대금...)는
    ev.raw.volume_power 처럼 필요할 때 꺼내 쓴다."""
    return Tick(
        kind="tick",
        symbol=e.stock_code,
        dt=parse_hhmmss(e.execution_time, on),
        raw=e,                                  # 원본 통째로 보관
        recv_dt=_parse_recv(e.datetime),        # 로컬 수신 시각(서브초 있음)
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
        recv_dt=_parse_recv(b.datetime),
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
        recv_dt=_parse_recv(n.datetime),
        order_no=n.ODER_NO,
        rejected=(n.RFUS_YN == "Y"),
        filled_qty=_f(n.CNTG_QTY) if filled else 0.0,
        price=_f(n.CNTG_UNPR),
    )
