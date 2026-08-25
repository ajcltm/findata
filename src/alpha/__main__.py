"""
═══════════════════════════════════════════════════════════════════
 alpha/__main__.py — AlphaTrader 구동 진입점
═══════════════════════════════════════════════════════════════════

    python -m alpha live [--real]     실전 (KIS 실계좌, 기본은 dry-run)
    python -m alpha sim               모의투자 (실시간 시세 + 가짜 주문)
    python -m alpha backtest [--csv]  백테스트 (CSV 없으면 합성 데이터)

■ 조립 순서 (live / sim)
    ① KiSEngine 생성        — 구독 종목 + 웹소켓 설정만. 아직 아무것도 안 켜진다
    ② AlphaTrader 생성 + 전략 등록
    ③ trader.run_live/run_sim(kis.market_event_queue) — 소비 스레드 기동
       (이 시점엔 큐가 비어 있어도 된다 — kis.run() 이 나중에 채운다)
    ④ kis.run(...) — 파싱/이벤트 스레드 + 웹소켓 기동. 메인 스레드를 블록한다

■ 세 실행 경로가 같은 전략 조합을 쓴다
    build_trader() 하나만 세 경로 모두에 넘긴다. 그래야 백테스트 성과와
    실전/모의 성과가 같은 조건을 재는 것이 된다.

■ 레코더 구독 / 뷰 구독도 이 파일 한 곳에서 정한다
    무엇을 어디에 저장할지(레코더), 콘솔에서 무엇을 몇 번 키로 볼지(뷰)는
    kis_engine.py/alphatrader.py 안에 숨어 있지 않다 — build_trader() 가
    trader.add_recording()/trader.add_view() 로 등록하고, build_recording()
    이 kis.recorder(원본 틱)를 등록한다. live/sim 둘 다 이 등록을 그대로
    쓴다(백테스트는 KiSEngine이 없어서 build_recording() 은 안 쓴다).
"""

from __future__ import annotations

import argparse
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path

from kis import kis_config
from kis import kis_logger
from kis import kis_parser
from kis.kis_engine import KiSEngine

from alpha.alphatrader.alphatrader import AlphaTrader
from alpha.events import events
from alpha.recording.sinks import SqliteSink
from alpha.strategy.indicator_watcher import IndicatorWatcher
from alpha.strategy.sma_cross_atr import SmaCrossATR
from alpha.strategy.spread_watcher import SpreadWatcher
from alpha.trader.trading import IndicatorSnapshot, Fill, Trade
from alpha.view import model

log = logging.getLogger("main")

SYMBOL = [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "035420",  # NAVER
        "035720",  # 카카오
        "005380",  # 현대차
        "000270",  # 기아
        "012330",  # 현대모비스
        "068270",  # 셀트리온
        "105560",  # KB금융
        "055550",  # 신한지주
        "086790",  # 하나금융지주
        "316140",  # 우리금융지주
        "005490",  # POSCO홀딩스
        "051910",  # LG화학
        "006400",  # 삼성SDI
        "003550",  # LG
        "066570",  # LG전자
        "034730",  # SK
    ]

BAR_SECONDS = 60


# ═══════════════════════════════════════════════════════════════════
# 0. 로깅 — 실행마다 logs/ 밑에 새 파일을 만든다
# ═══════════════════════════════════════════════════════════════════

def setup_logging(mode: str, verbose: bool) -> Path:
    """이번 실행 전용 로그 파일을 새로 만들어 루트 로거에 붙인다.

    kis_websocket/kis_engine/AlphaTrader/LiveRunner/SimBroker 는 전부
    logging.getLogger(...) 로만 로그를 남기고 자기 핸들러가 없다(kis 로거만
    예외). 루트에 핸들러가 없으면 그 로그들은 어디에도 남지 않고 사라진다.
    여기서 콘솔 + 파일 핸들러를 루트에 달아, 파이프라인 어디서 로그를 찍든
    한 파일에서 시간순으로 확인할 수 있게 한다."""
    kis_config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = kis_config.LOG_DIR / f"alpha_{mode}_{datetime.now():%Y%m%d_%H%M%S}.log"

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)

    # console_handler = logging.StreamHandler()
    # console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(file_handler)
    # root.addHandler(console_handler)

    kis_logger.setup_logger(kis_config.LOG_DIR)   # kis 전용 로거(kis.log)도 그대로 유지

    log.info("=" * 56)
    log.info("AlphaTrader 실행 시작 — mode=%s, 로그 파일: %s", mode, log_path)
    log.info("=" * 56)
    return log_path


# ═══════════════════════════════════════════════════════════════════
# 1. 전략 + 레코더 + 뷰 등록 — 세 실행 경로의 유일한 공통 정의
# ═══════════════════════════════════════════════════════════════════

def build_trader(simul_mode: bool) -> AlphaTrader:
    trader = AlphaTrader()

    trader.add_strategy("추세", SmaCrossATR(symbol=SYMBOL[0]),
                        allocation=5_000_000,
                        bars=[(SYMBOL[0], BAR_SECONDS)],
                        warmup=20)                  # 지표가 데워질 때까지 주문 차단
    trader.add_strategy("호가감시", SpreadWatcher(),
                        allocation=0,                # 주문 안 내므로 0
                        quotes=SYMBOL)

    # 지표 뷰용 — 종목마다 독립된 인스턴스로 등록해야 값이 안 섞인다
    # (IndicatorWatcher 문서 참고). "추세"는 SYMBOL[0]을 이미 커버하지만
    # 매매용 인스턴스라 이 목적으로 재사용하지 않는다.
    for symbol in SYMBOL:
        trader.add_strategy(f"지표감시_{symbol}", IndicatorWatcher(symbol=symbol),
                            allocation=0,
                            bars=[(symbol, BAR_SECONDS)])

    # ── 레코더 구독 — AlphaTrader(Engine)가 다루는 데이터를 어디에 저장할지.
    #    Bar/IndicatorSnapshot은 Engine.feed()/Trader 안에서만 만들어지는
    #    데이터라 여기가 유일한 저장 지점이다. Tick/Quote는 build_recording(kis)가
    #    kis_data.db에 원본(파싱 직후)을 이미 저장하지만, 여기서도 한 번 더
    #    alpha_data.db에 남긴다 — Engine.feed()를 통과한(=전략에 실제로
    #    배달된) 이벤트라서 kis_data.db 쪽과 타이밍/필터링이 다를 수 있다.
    db_name = "alpha_data_sim.db" if simul_mode else "alpha_data.db"
    alpha_sink = SqliteSink(str(kis_config.DATA_DIR / db_name))

    trader.add_recording(Fill, alpha_sink, name="fill")

    # Trade(라운드트립 완결)는 strategy_id 를 자기 필드로 갖는다
    # (Trader.feed_fill() 이 채워 넣는다) — extra 로 주지 않는다. extra 는
    # 구독 채널 하나에 고정되는 값이라, 거래를 내는 전략이 둘 이상이면
    # 같은 Recorder 에 Trade 를 또 구독해 extra 만 다르게 줘도 안 나뉜다
    # (Recorder 가 같은 타입의 모든 구독 채널에 레코드를 전부 복사해
    # 뿌리기 때문에, 그러면 서로 다른 전략의 거래가 양쪽 테이블에 겹쳐
    # 들어가고 strategy_id 도 뒤섞인다). 필드로 두면 전략이 몇 개든 안전하다.
    trader.add_recording(Trade, alpha_sink, name="trade")

    trader.add_recording(IndicatorSnapshot, alpha_sink, name="indicator")
    trader.add_recording(events.Bar, alpha_sink, name="bar")
    trader.add_recording(events.Tick, alpha_sink, name="tick")
    trader.add_recording(events.Quote, alpha_sink, name="quote")
    # events.Notice — 실전(KiSEngine)/모의(SimBroker) 체결통보가 이제
    # 하나의 정규화된 타입이라 여기 한 번만 등록하면 둘 다 잡힌다.
    trader.add_recording(events.Notice, alpha_sink, name="notice")

    # ── 콘솔 뷰 구독 — 등록 순서가 곧 'v' 화면의 숫자키(1,2,3...) ──
    trader.add_view(Fill, model.Recent(100000, cols=[
        "dt", "symbol", "side", "size", "price", "order_id", "commission"]), name="체결(fill)")
    trader.add_view(Trade, model.Recent(100000, cols=[
        "strategy_id", "symbol", "size", "entry_dt", "entry_price",
        "exit_dt", "exit_price", "gross_pnl", "commission"]), name="거래(trade)")
    trader.add_view(events.Notice, model.Recent(100000, cols=[
        "dt", "symbol", "order_no", "filled_qty", "price", "rejected"]),
        name="체결통보")
    trader.add_view(events.Tick, model.Board(cols=["price", "volume"]),
                    name="시세판")
    trader.add_view(events.Quote, model.Latest(), name="호가")
    trader.add_view(events.Bar, model.Recent(20, cols=[
        "symbol", "open", "high", "low", "close", "volume"]), name="봉")
    trader.add_view(IndicatorSnapshot, model.Pivot(), name="지표")
    return trader


def build_recording(kis: KiSEngine, simul_mode: bool) -> None:
    """KiSEngine이 만드는 원본 틱/이벤트를 어디에 저장할지 등록한다.

    live/sim 에서만 부른다 — 백테스트는 KiSEngine 자체가 없다.
    kis.run() 이 스레드를 켜기 전(=아직 데이터가 안 들어오는 동안)에
    끝내면 되므로, 여기서 등록하고 바로 kis.run() 을 부르면 된다."""
    db_name = "kis_data_sim.db" if simul_mode else "kis_data.db"
    sink = SqliteSink(str(kis_config.DATA_DIR / db_name))
    kis.recorder.subscribe(kis_parser.Execution, sink, name="execution")
    kis.recorder.subscribe(kis_parser.OrderBook, sink, name="orderbook")
    kis.recorder.subscribe(kis_parser.Notice, sink, name="notice")


# ═══════════════════════════════════════════════════════════════════
# 2. 실전 / 모의 — KiSEngine 의 market_event_queue 를 AlphaTrader 에 주입
# ═══════════════════════════════════════════════════════════════════

def run_live(dry_run: bool):
    simul_mode = False
    log.info("run_live 시작 (dry_run=%s)", dry_run)
    kis = KiSEngine(price_codes=SYMBOL, orderbook_codes=SYMBOL, simul_mode=simul_mode)
    build_recording(kis, simul_mode=simul_mode)
    trader = build_trader(simul_mode=simul_mode)

    # on_quit=kis.stop : 콘솔에서 'q'를 누르면 웹소켓만 세운다.
    # 그래야 아래 kis.run()의 블로킹이 풀리고, 다운스트림 드레인·flush는
    # 여기(메인 스레드)에서 kis.run() 리턴 뒤에 마저 처리한다 —
    # 뷰가 뜬 daemon 스레드에서 다 끝내려 하면, 메인 스레드가 먼저 빠져나가
    # daemon 스레드가 중간에 죽어 마지막 몇 건이 유실될 수 있다.
    runner = trader.run_live(kis.market_event_queue, dry_run=dry_run, on_quit=kis.stop)

    # trading/show 는 KiSEngine 자체 기능이라 여기선 안 쓴다 —
    # 주문 경로는 이미 AlphaTrader.run_live() 가 맡았다.
    # recording 은 켠다 — data/kis_data.db 에 원본 틱·이벤트가 쌓인다.
    kis.run(recording=True, trading=False, show=False)   # 블로킹 — q → kis.stop() 이 풀어준다

    # 웹소켓이 끝났다(q 종료 또는 오류) — trading_q/지표를 마저 비우고 저장한다.
    runner.stop()
    trader.stop_recording()


def run_sim(simul):
    simul_mode = simul
    log.info("run_sim 시작")
    kis = KiSEngine(price_codes=SYMBOL, orderbook_codes=SYMBOL, simul_mode=simul_mode)
    build_recording(kis, simul_mode=simul_mode)
    trader = build_trader(simul_mode=simul_mode)

    runner = trader.run_sim(kis.market_event_queue, on_quit=kis.stop)
    kis.run(recording=True, trading=False, show=False)   # 블로킹 — q → kis.stop() 이 풀어준다

    runner.stop()
    trader.stop_recording()


# ═══════════════════════════════════════════════════════════════════
# 3. 백테스트 — 과거 데이터는 호출자가 준비한다
# ═══════════════════════════════════════════════════════════════════

def _synthetic_bars(n: int = 200):
    """CSV 없이도 데모가 돌아가도록 사인파 + 완만한 상승을 합성한다."""
    import pandas as pd

    base = datetime(2024, 1, 15, 9, 0)
    idx, rows = [], []
    for i in range(n):
        px = 71000 + math.sin(i / 12) * 2500 + i * 20
        px = round(px / 10) * 10
        idx.append(base + timedelta(minutes=i))
        rows.append(dict(open=px, high=px * 1.002, low=px * 0.998,
                         close=px, volume=1000))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def _load_dataframe(path: str | None):
    if not path:
        return _synthetic_bars()
    import pandas as pd
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]      # OHLCV 소문자로 통일
    return df


def run_backtest(path: str | None, plot: bool):
    trader = build_trader()
    df = _load_dataframe(path)

    result, eng = trader.run_backtest(df, symbol=SYMBOL, seconds=BAR_SECONDS, plot=plot)

    print("\n" + "═" * 58)
    print("  AlphaTrader 백테스트")
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
    print("═" * 58)
    return eng


# ═══════════════════════════════════════════════════════════════════
# 4. 엔트리
# ═══════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="AlphaTrader 구동")
    ap.add_argument("mode", choices=["live", "sim", "backtest"])
    ap.add_argument("--csv", help="백테스트용 봉 CSV (없으면 합성 데이터)")
    ap.add_argument("--plot", action="store_true", help="백테스트 차트 표시")
    ap.add_argument("--real", action="store_true",
                    help="live 모드에서 실주문 활성화 (기본은 dry-run)")
    ap.add_argument("--simul", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.mode, args.verbose)

    if args.mode == "live":
        run_live(dry_run=not args.real)
    elif args.mode == "sim":
        run_sim(simul=args.simul)
    else:
        run_backtest(args.csv, args.plot)


if __name__ == "__main__":
    main()
