"""
═══════════════════════════════════════════════════════════════════
 alphatrader.py — 실전·모의·백테스트를 하나로 묶는 파사드
═══════════════════════════════════════════════════════════════════

■ 이 파일이 하는 일
    전략 등록(add_strategy)과 실행 경로 선택(run_live/run_sim/run_backtest)을
    분리한다. 등록은 세 경로에서 완전히 같아야 성과를 비교할 수 있으므로
    한 곳(AlphaTrader._specs)에 모아두고, 실행 방식만 메서드로 고른다.

■ 데이터 주입은 호출자(메인 파일) 책임이다
    run_live / run_sim : KiSEngine.market_event_queue 를 메인 파일이 만들어
                          넘긴다. AlphaTrader 는 웹소켓을 직접 켜지 않는다
                          — 소켓 수명주기(KiSEngine)와 전략 조립(AlphaTrader)의
                          책임을 섞으면, market_event_queue 를 다른 소스
                          (리플레이 등)로 채우고 싶을 때 파사드를 못 쓴다.
    run_backtest        : 과거 OHLCV DataFrame 을 메인 파일이 넘긴다.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from kis import kis_config

from alpha.engine.engine import Engine
from alpha.engine.live_runner import LiveRunner
from alpha.broker.kis_broker import KISBroker
from alpha.broker.sim_broker import SimBroker
from alpha.trader.trading import Strategy, IndicatorSnapshot
from alpha.recording.recorder import Recorder
from alpha.recording.sinks import SqliteSink

log = logging.getLogger("main")


# ═══════════════════════════════════════════════════════════════════
# AlphaTrader — 파사드
# ═══════════════════════════════════════════════════════════════════

class AlphaTrader:
    """전략을 등록해두고, 실전(run_live)/모의(run_sim)/백테스트(run_backtest)
    중 원하는 경로로 실행하는 파사드.

    ■ 왜 등록과 실행을 분리하나
        add_strategy() 로 쌓인 목록은 세 실행 경로 모두에서 완전히 같은
        조합으로 Engine.add() 된다. 경로별로 전략을 따로 등록하면
        백테스트 성과와 실전 성과가 애초에 다른 걸 재는 셈이 된다.

    사용 예:
        from alpha.strategy.sma_cross_atr import SmaCrossATR
        from alpha.strategy.spread_watcher import SpreadWatcher

        trader = AlphaTrader()
        trader.add_strategy("추세", SmaCrossATR(symbol="005930"),
                            allocation=5_000_000, bars=[("005930", 60)], warmup=20)
        trader.add_strategy("호가감시", SpreadWatcher(), allocation=0, quotes=["005930"])

        # 실전/모의 — market_event_queue 는 메인 파일이 KiSEngine 에서 만든다
        kis = KiSEngine(price_codes=[...], orderbook_codes=[...])
        runner = trader.run_live(kis.market_event_queue)

        # 백테스트 — 과거 데이터도 메인 파일이 만든다
        result, eng = trader.run_backtest(df)
    """

    def __init__(self, state_dir: Optional[str] = None):
        self.state_dir = state_dir
        self._specs: list[dict] = []

        # 등록된 전략들의 지표 값(IndicatorSnapshot)을 저장한다.
        # data/kis_data.db(시세 원본)와 별개 파일 — 지표는 전략에 딸린
        # 파생 데이터라 소스(KiSEngine)와 소비자(AlphaTrader)를 나눠 둔다.
        self._recorder = Recorder()
        self._recorder.subscribe(IndicatorSnapshot,
                                 SqliteSink(str(kis_config.DATA_DIR / "alpha_data.db")),
                                 name="indicator")

    # ── 전략 등록 ────────────────────────────────────────────────
    def add_strategy(self, strategy_id: str, strategy: Strategy,
                      allocation: float,
                      ticks: list[str] = (), quotes: list[str] = (),
                      bars: list[tuple[str, int]] = (),
                      warmup: int = 0, history: list = ()) -> "AlphaTrader":
        """Engine.add() 로 그대로 전달될 인자를 쌓아둔다.
        실행은 run_live/run_sim/run_backtest 가 부를 때 비로소 일어난다."""
        self._specs.append(dict(
            strategy_id=strategy_id, strategy=strategy, allocation=allocation,
            ticks=list(ticks), quotes=list(quotes), bars=list(bars),
            warmup=warmup, history=list(history),
        ))
        log.info("전략 등록: %s (%s) allocation=%s ticks=%s quotes=%s bars=%s",
                strategy_id, type(strategy).__name__, allocation,
                list(ticks), list(quotes), list(bars))
        return self

    def _build_engine(self, broker, dry_run: bool) -> Engine:
        """실전/모의/백테스트가 공유하는 조립 지점.
        bt_broker.run_backtest 가 요구하는 build_engine(broker, dry_run) 시그니처와
        맞춰뒀다 — 백테스트 경로도 이 메서드를 그대로 넘겨 쓴다."""
        self._recorder.start()   # 이미 떠 있으면 no-op
        eng = Engine(real_broker=broker, dry_run=dry_run, state_dir=self.state_dir,
                    recorder=self._recorder)
        for spec in self._specs:
            eng.add(spec["strategy_id"], spec["strategy"],
                     allocation=spec["allocation"], ticks=spec["ticks"],
                     quotes=spec["quotes"], bars=spec["bars"],
                     warmup=spec["warmup"], history=spec["history"])
        return eng

    # ── 실행 경로 ────────────────────────────────────────────────
    def run_live(self, market_event_queue, dry_run: bool = True,
                 business_date: Optional[date] = None,
                 kill_file: str = "./STOP_TRADING") -> LiveRunner:
        """실계좌. market_event_queue 는 KiSEngine.market_event_queue —
        웹소켓 수신·파싱·정규화가 이미 끝난 MarketEvent/Notice 스트림이다."""
        log.info("AlphaTrader.run_live 시작 — 전략 %d개, dry_run=%s", len(self._specs), dry_run)
        broker = KISBroker()
        eng = self._build_engine(broker, dry_run)
        runner = LiveRunner(engine=eng, trading_q=market_event_queue, broker=broker,
                            test_mode=False, dry_run=dry_run,
                            business_date=business_date, kill_file=kill_file)
        runner.start()
        log.info("AlphaTrader.run_live — LiveRunner 기동 완료")
        return runner

    def run_sim(self, market_event_queue, cash: float = 10_000_000,
                business_date: Optional[date] = None,
                kill_file: str = "./STOP_TRADING",
                **sim_broker_kwargs) -> LiveRunner:
        """모의투자. 실시간 market_event_queue 로 시세를 받되 주문은 가짜다.

        ★ SimBroker 가 같은 큐에 체결통보를 되돌려 넣는다 ★
          fill_q=market_event_queue 로 넘기면 시세와 모의체결이 한 큐에서
          순서대로 나온다 — LiveRunner 가 실전과 똑같은 소비 루프로 처리한다."""
        log.info("AlphaTrader.run_sim 시작 — 전략 %d개, cash=%s", len(self._specs), cash)
        broker = SimBroker(fill_q=market_event_queue, cash=cash, **sim_broker_kwargs)
        eng = self._build_engine(broker, dry_run=False)   # 모의는 항상 가짜주문을 낸다
        runner = LiveRunner(engine=eng, trading_q=market_event_queue, broker=broker,
                            test_mode=True, dry_run=False,
                            business_date=business_date, kill_file=kill_file)
        runner.start()
        log.info("AlphaTrader.run_sim — LiveRunner 기동 완료")
        return runner

    def run_backtest(self, data, symbol: str = "005930", seconds: int = 60,
                      cash: float = 10_000_000, commission: float = 0.00015,
                      slippage: float = 0.001, plot: bool = False,
                      analyzers: bool = True):
        """백테스트. data 는 호출자가 준비한 과거 OHLCV —
        단일 DataFrame 또는 {종목코드: DataFrame}.

        backtrader 는 이 경로에서만 필요하므로, run_live/run_sim 만 쓰는
        환경에 backtrader 가 없어도 되도록 여기서 지역 import 한다."""
        log.info("AlphaTrader.run_backtest 시작 — 전략 %d개, symbol=%s seconds=%d",
                len(self._specs), symbol, seconds)
        from alpha.broker.bt_broker import run_backtest as _run_backtest
        try:
            return _run_backtest(
                self._build_engine, data, symbol=symbol, seconds=seconds,
                cash=cash, commission=commission, slippage=slippage,
                plot=plot, analyzers=analyzers)
        finally:
            # 백테스트는 한 프로세스 안에서 끝나므로 여기서 바로 flush.
            # run_live/run_sim 은 오래 켜 두므로 stop_recording() 을 따로 부를 것.
            self._recorder.stop()

    # ── 지표 기록 종료 ────────────────────────────────────────────
    def stop_recording(self):
        """지표 기록 스레드를 정지하고 남은 배치를 마저 쓴다.

        run_live/run_sim 은 오래 켜 두는 프로세스라 언제 끝날지 이 클래스가
        모른다 — 호출자가 종료 시점(신호 핸들러 등)에 불러야 한다.
        안 부르면 마지막 배치(최대 batch 건)를 잃는다."""
        self._recorder.stop()
