"""
═══════════════════════════════════════════════════════════════════
 main.py — 전략 정의 + 진입점
═══════════════════════════════════════════════════════════════════

    python main.py backtest     backtrader + Engine  (봉)
    python main.py live         실전 (기존 KiSEngine 에 연결)

■ 백테스트와 실전이 같은 Engine 을 쓴다
    build_engine(broker, dry_run) 하나만 양쪽에 넘긴다. 다른 건 브로커뿐이다.

        백테스트 : cerebro → _Pump → Engine → BacktraderBroker
        실전     : 소켓 → trading_q → EngineTrader → Engine → KISBroker

    전략 코드도, 지표도, 손익 계산(TradeTracker)도 전부 공유한다.

■ backtrader 의 한계 하나
    데이터피드가 OHLCV 만 실어 나른다. 호가(Quote) 이벤트는 재생할 수 없어서
    on_quote 만 구현한 전략은 백테스트에서 호출되지 않는다.
    (등록은 되고 주문도 안 내므로 에러는 나지 않는다)

■ 실전 연결 (기존 findata 구조 그대로)
    KisFeed(웹소켓) ──put──> trading_q ──get──> EngineTrader ──> Engine

    kis_engine.py 의 start_trading() 만 고치면 된다:

        from kis import kis_bridge

        def start_trading(self):
            trader = kis_bridge.EngineTrader(
                strategy=self.strategy,          # ← build_engine 팩토리
                trading_q=self.consumer_queue['trading_q'],
                parser=self.parser,
                stop_event=self._stop,
                test_mode=self.simul_mode)
            threading.Thread(target=trader.trading, daemon=True).start()

    그리고 실행부:
        eng = KiSEngine(price_codes=["005930"], orderbook_codes=["005930"])
        eng.add_strategy(build_engine)           # 전략 클래스 대신 팩토리
        eng.run(recording=True, trading=True, show=False)
"""

from __future__ import annotations

import argparse
import logging
import math
from datetime import date, datetime, timedelta

from engine import Engine
from indicators import ATR, SMA, CrossOver
from trading import Strategy

log = logging.getLogger("main")


# ═══════════════════════════════════════════════════════════════════
# 1. 전략 — 모든 경로가 이 클래스를 공유한다
# ═══════════════════════════════════════════════════════════════════

class SmaCrossATR(Strategy):
    """이평 교차 진입 + ATR 트레일링 스탑. 봉 기반 추세추종.

    진입: 단기선이 장기선을 상향 돌파
    청산: ① 하향 돌파(추세 끝)  ② 스탑 터치(급락)
    """

    defaults = dict(symbol="005930", fast=5, slow=20,
                    atr_period=10, size_pct=0.9, stop_atr=2.0)

    def setup(self):
        self.fast = self.ind(SMA(self.p.fast))
        self.slow = self.ind(SMA(self.p.slow))
        self.cross = self.ind(CrossOver(self.fast, self.slow))   # 입력 지표 뒤에
        self.atr = self.ind(ATR(self.p.atr_period))
        self.stop_price = None

    def on_bar(self, bar):
        b, sym = self.broker, self.p.symbol
        pos = b.position(sym)

        if pos.is_flat:
            if self.cross.value > 0:
                # 스탑 = 현재가 - ATR×2. 평소 움직임의 2배만큼 반대로 가면
                # 판단이 틀렸다고 본다. 종목마다 자동으로 폭이 맞춰진다.
                self.stop_price = bar.close - self.p.stop_atr * self.atr.value
                b.target_pct(sym, self.p.size_pct, tag="entry")
        else:
            hit = self.stop_price is not None and bar.close <= self.stop_price
            if self.cross.value < 0 or hit:
                b.close(sym, tag="stop" if hit else "cross")
                self.stop_price = None
            else:
                new_stop = bar.close - self.p.stop_atr * self.atr.value
                if self.stop_price is None or new_stop > self.stop_price:
                    self.stop_price = new_stop          # ★ 오를 때만 올린다

    def on_order(self, o):
        if o.status.value == "filled":
            log.info("  체결 %s %d주 @%s", o.side.value, int(o.filled_size),
                     f"{o.avg_fill_price:,.0f}")
        elif o.status.value == "rejected":
            log.warning("  주문거부 %s: %s", o.id, o.reject_reason)

    def on_trade(self, t):
        log.info("  ★ 거래완료 %s원 (%s)",
                 f"{t.pnl:+,.0f}", f"{t.pnl_pct:+.2%}")

    def to_state(self):
        return {"stop_price": self.stop_price}      # 봉으로 복원 불가

    def from_state(self, s):
        self.stop_price = s.get("stop_price")


class SpreadWatcher(Strategy):
    """호가만 보는 전략. 주문은 내지 않는다.
    봉을 안 받는 전략도 공존한다는 예시. backtrader 경로에는 못 올라간다."""

    defaults = dict(threshold=0.3)

    def on_quote(self, q):
        if abs(q.imbalance) >= self.p.threshold:
            log.info("  [호가] 불균형 %s  스프레드 %s원",
                     f"{q.imbalance:+.2f}", f"{q.spread:,.0f}")


SYMBOL = "005930"
BAR_SECONDS = 60


# ═══════════════════════════════════════════════════════════════════
# 2. 전략 등록 — 백테스트와 실전의 유일한 공통 정의
# ═══════════════════════════════════════════════════════════════════

def build_engine(broker, dry_run: bool) -> Engine:
    """★ 이 함수를 kis_engine.add_strategy() 에 그대로 넘긴다 ★

    백테스트든 실전이든 여기 하나만 부른다. 여기가 갈리면 의미가 없다."""
    eng = Engine(broker, dry_run=dry_run)

    # history 를 넘기면 지표를 미리 데운다. 백테스트는 backtrader 가
    # 과거 봉을 전부 흘려주므로 비워두고 warmup 카운터로 막는다.
    # 실전에서는 REST 로 받은 과거 봉을 여기 넣으면 기동 즉시 매매 가능하다.
    #     need = SmaCrossATR().required_history()   # {("bar",60): 21, ...}
    #     hist = fetch_bars(SYMBOL, 60, need[("bar", 60)])
    eng.add("추세", SmaCrossATR(symbol=SYMBOL),
            allocation=5_000_000,
            bars=[(SYMBOL, BAR_SECONDS)],
            warmup=20,                  # 지표가 데워질 때까지 주문 차단
            history=())

    eng.add("호가감시", SpreadWatcher(),
            allocation=0,               # 주문 안 내므로 0
            quotes=[SYMBOL])

    return eng


# ═══════════════════════════════════════════════════════════════════
# 3. 데이터 — 두 경로가 같은 가격 시리즈를 쓴다 (대조검증의 전제)
# ═══════════════════════════════════════════════════════════════════

def price_series(n: int = 200) -> list[tuple[datetime, float]]:
    """사인파 + 완만한 상승. 교차와 스탑이 몇 번 발동하도록."""
    base = datetime(2024, 1, 15, 9, 0)
    out = []
    for i in range(n):
        px = 71000 + math.sin(i / 12) * 2500 + i * 20
        out.append((base + timedelta(minutes=i), round(px / 10) * 10))
    return out


def load_dataframe(path: str | None = None):
    """봉 DataFrame. 없으면 합성 시리즈.

    실제 연결:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]   # OHLCV 소문자
        return df

    ★ price_series() 를 한 번만 부르는 게 중요하다 ★
      두 번 부르면 rows 와 idx 가 서로 다른 시리즈가 될 수 있다.
    """
    import pandas as pd
    if path:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]
        return df

    series = price_series()
    idx = [dt for dt, _ in series]
    rows = [dict(open=px, high=px * 1.002, low=px * 0.998,
                 close=px, volume=1000)
            for _, px in series]
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


# ═══════════════════════════════════════════════════════════════════
# 4. 백테스트 — cerebro 가 루프를 돌고 _Pump 가 Engine 을 먹인다
# ═══════════════════════════════════════════════════════════════════

def run_backtest_cmd(path: str | None = None, plot: bool = False,
                     dump: str | None = None):
    from bt_broker import run_backtest

    result, eng = run_backtest(
        build_engine,                   # ★ 실전과 똑같은 팩토리
        load_dataframe(path),
        symbol=SYMBOL,
        seconds=BAR_SECONDS,            # Engine 의 bars=[(sym, 60)] 과 일치
        cash=10_000_000,
        plot=plot,
    )

    print("\n" + "═" * 58)
    print("  backtrader + Engine")
    print("═" * 58)
    for sid, slot in eng.slots.items():
        v = eng.snapshot()[sid]
        if v["allocation"] == 0:
            print(f"  [{sid}] 관찰 전용 (주문 없음)")
            continue
        st = slot.trader.tracker.stats()
        print(f"  [{sid}] 평가 {v['equity']:,.0f}원 ({v['pnl_pct']:+.2%})")
        if st["trades"]:
            # 라운드트립 단위라 분할 청산을 해도 거래 1건으로 센다
            print(f"        거래 {st['trades']}건  승률 {st['win_rate']:.0%}  "
                  f"손익비 {st['profit_factor']:.2f}")
            print(f"        실현손익 {st['realized_pnl']:+,.0f}원  "
                  f"비용 {st['total_cost']:,.0f}원")
    print("─" * 58)
    an = result.analyzers
    sharpe = an.sharpe.get_analysis().get("sharperatio")
    dd = an.dd.get_analysis().max.drawdown
    print(f"  샤프 {sharpe if sharpe is None else round(sharpe, 3)}   "
          f"MDD {dd:.2f}%")
    print("═" * 58)

    if dump:
        # 체결 원장과 거래 목록을 CSV 로. 사후 분석용.
        eng.dump(dump)
    return eng


# ═══════════════════════════════════════════════════════════════════
# 7. 실전 — 기존 KiSEngine 에 연결
# ═══════════════════════════════════════════════════════════════════

def run_live(real_orders: bool = False):
    """기존 findata 구조를 그대로 쓴다. 이 함수는 배선 예시일 뿐,
    실제로는 기존 진입점에서 KiSEngine 을 띄우면 된다."""
    print(__doc__.split("■ 실전 연결")[1])
    print("\n  이 파일의 build_engine 을 add_strategy() 에 넘기면 됩니다.")
    if not real_orders:
        print("  test_mode=True(dry-run)로 며칠 관찰 후 enable_trading() 하세요.")


# ═══════════════════════════════════════════════════════════════════
# 6. 엔트리
# ═══════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="KIS 트레이딩 엔진")
    ap.add_argument("mode", choices=["backtest", "live"])
    ap.add_argument("--csv", help="봉 CSV 파일 (없으면 합성 데이터)")
    ap.add_argument("--plot", action="store_true", help="차트 표시")
    ap.add_argument("--dump", metavar="PREFIX",
                    help="체결·거래 내역을 CSV 로 저장 (예: --dump session)")
    ap.add_argument("--real", action="store_true", help="실주문 활성화")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s")

    if args.mode == "backtest":
        run_backtest_cmd(args.csv, args.plot, args.dump)
    else:
        run_live(args.real)


if __name__ == "__main__":
    main()