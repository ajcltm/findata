"""T-001 재현: SimBroker 지정가 체결가가 시세가 아니라 주문가로 잡히는지 본다.

- SimBroker 와 이벤트 타입만 쓴다. KIS API, 웹소켓, DB 를 부르지 않는다.
- 사용: py -3.12 repro_limit_fill.py <프로젝트 루트>
"""
import sys
from datetime import datetime, timedelta
from queue import Queue

sys.path.insert(0, f"{sys.argv[1] if len(sys.argv) > 1 else '.'}/src")

from alpha.broker.sim_broker import SimBroker            # noqa: E402
from alpha.core.orders import Order, OrderType, Side      # noqa: E402
from alpha.events.events import Tick                       # noqa: E402

T0 = datetime(2026, 8, 27, 9, 20, 50)


def tick(sec, px, bid, ask):
    return Tick(kind="tick", symbol="005930", dt=T0 + timedelta(seconds=sec),
                price=px, volume=1, bid=bid, ask=ask)


def run(case, side, limit, ticks):
    q = Queue()
    b = SimBroker(fill_q=q, cash=10_000_000)
    if side is Side.SELL:                         # 매도 시험용 보유 1주
        b.from_state({"cash": 10_000_000,
                      "positions": {"005930": {"size": 1, "avg_price": 80000}}})
    b.on_market(ticks[0])
    o = b.submit(Order(symbol="005930", side=side, size=1,
                       type=OrderType.LIMIT if limit else OrderType.MARKET,
                       price=limit))
    for ev in ticks[1:]:
        b.on_market(ev)
    n = q.get_nowait() if not q.empty() else None
    print(f"{case}: 주문 {side.value} @{limit or '시장가'} / 시세 "
          f"{ticks[-1].price:,.0f} (bid {ticks[-1].bid:,.0f}, ask {ticks[-1].ask:,.0f})"
          f" -> 체결가 {n.price if n else None}")
    return n.price if n else None


if __name__ == "__main__":
    ts = [tick(0, 80000, 79900, 80000), tick(1, 80000, 79900, 80000)]
    a = run("A 매수 지정가 100,000 (시세보다 25% 위)", Side.BUY, 100000, ts)
    b = run("B 매수 시장가 (비교)", Side.BUY, None, ts)
    c = run("C 매도 지정가 50,000 (시세보다 37.5% 아래)", Side.SELL, 50000, ts)
    ts0 = [tick(0, 80000, 79900, 80000), tick(1, 0.0, 79900, 80000)]
    d = run("D 매수 지정가 79,000 + 가격 0 틱", Side.BUY, 79000, ts0)
    ok = (a == 100000 and b == 80000 and c == 50000 and d == 79000)
    print("재현 성공" if ok else "재현 결과가 가설과 다름")
