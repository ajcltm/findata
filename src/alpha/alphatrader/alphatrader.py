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
from typing import Callable, Optional

from kis import kis_config

from alpha.engine.engine import Engine
from alpha.engine.live_runner import LiveRunner
from alpha.broker.kis_broker import KISBroker
from alpha.broker.sim_broker import SimBroker
from alpha.events import events
from alpha.recording.recorder import Recorder
from alpha.recording.sinks import SqliteSink
from alpha.strategy.manual import ManualStrategy, MANUAL_STRATEGY_ID
from alpha.trader.trading import (AccountSnapshot, Fill, IndicatorSnapshot, Order,
                                   Position, Strategy, Trade)
from alpha.view.app import Application
from alpha.view.model import Aggregator, Board, Latest, Pivot, Recent

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
        self._view_specs: list[dict] = []
        # 기본 뷰(_ensure_default_recording_and_views)는 build_trader() 가
        # 언제 add_view() 를 부르든(그 전이든 후든) 항상 먼저 화면 번호를
        # 차지하도록 별도 목록에 쌓아둔다 — _apply_view() 에서 이 목록을
        # 사용자가 등록한 _view_specs 보다 앞에 재생한다.
        self._default_view_specs: list[dict] = []

        # 지표 값(IndicatorSnapshot) 등, 전략이 만들어내는 파생 데이터를
        # 저장할 레코더. data/kis_data.db(시세 원본)와 별개인 이유는 지표가
        # 전략에 딸린 파생 데이터라 소스(KiSEngine)와 소비자(AlphaTrader)를
        # 나눠서다. 표준 레코딩/뷰는 _ensure_default_recording_and_views() 가
        # 자체 등록한다 — build_trader() 자리에는 전략별로 다른 것만 남긴다
        # (예: MACD처럼 라인이 여럿인 지표 전용 뷰).
        self._recorder = Recorder()
        self._defaults_built = False

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

    def _ensure_manual_strategy(self) -> None:
        """콘솔 수동 주문의 그릇이 되는 전략을 한 번만 등록한다.

        배정자본을 무제한(float("inf"))으로 주면 StrategyBroker.submit()의
        예산 검사('필요금액 > 가용현금')가 항상 False가 되어 실질적으로
        막히지 않는다 — Broker/StrategyBroker 코드를 하나도 안 건드리고
        '무제한'을 표현하는 가장 단순한 방법이다.

        run_live/run_sim에서만 부른다 — 백테스트에는 콘솔이 없어 이 슬롯을
        쓸 일이 없고, allocation=inf인 슬롯이 백테스트 리포트에
        nan%/inf원으로 잡히는 것도 피한다."""
        if any(s["strategy_id"] == MANUAL_STRATEGY_ID for s in self._specs):
            return                                  # 이미 등록됨 — 멱등
        self.add_strategy(MANUAL_STRATEGY_ID, ManualStrategy(),
                          allocation=float("inf"))

    def _ensure_default_recording_and_views(self, simul_mode: bool) -> None:
        """어떤 전략 조합이든 항상 똑같이 필요한 레코더/뷰 구독을 자체 등록한다.

        ★ build_trader() 자리에는 무엇만 남기나 ★
            ① 전략 등록(add_strategy) — 프로젝트마다 다르다.
            ② MACD처럼 라인이 여럿인 지표의 전용 뷰 — label이 그 지표
               구현(IndicatorWatcher.setup())에 종속돼 있어서 여기서
               일반화할 수 없다.
            나머지(Fill/Trade/IndicatorSnapshot(단일 라인)/Bar/Tick/Quote/
            Notice 레코딩과 그 표준 뷰)는 어떤 전략을 쓰든 항상 같으므로
            여기 한 곳에 둔다 — build_trader() 를 고칠 때마다 매번
            그대로 옮겨 적어야 했던 보일러플레이트를 없앤다.

        run_live/run_sim/run_backtest 가 알아서 부른다. 멱등이라 여러 번
        불려도 두 번 등록되지 않는다."""
        if self._defaults_built:
            return
        self._defaults_built = True

        # ── 레코더 구독 — Engine.feed()/Trader 안에서만 만들어지는 데이터를
        #    어디에 저장할지. Tick/Quote는 build_recording(kis)가 kis_data.db에
        #    원본(파싱 직후)을 이미 저장하지만, 여기서도 한 번 더 alpha_data.db에
        #    남긴다 — Engine.feed()를 통과한(=전략에 실제로 배달된) 이벤트라서
        #    kis_data.db 쪽과 타이밍/필터링이 다를 수 있다.
        db_name = "alpha_data_sim.db" if simul_mode else "alpha_data.db"
        alpha_sink = SqliteSink(str(kis_config.DATA_DIR / db_name))

        self.add_recording(Fill, alpha_sink, name="fill")
        # Trade(라운드트립 완결)는 strategy_id 를 자기 필드로 갖는다
        # (Trader.feed_fill() 이 채워 넣는다) — extra 로 주지 않는다. extra 는
        # 구독 채널 하나에 고정되는 값이라, 거래를 내는 전략이 둘 이상이면
        # 같은 Recorder 에 Trade 를 또 구독해 extra 만 다르게 줘도 안 나뉜다
        # (Recorder 가 같은 타입의 모든 구독 채널에 레코드를 전부 복사해
        # 뿌리기 때문에, 그러면 서로 다른 전략의 거래가 양쪽 테이블에 겹쳐
        # 들어가고 strategy_id 도 뒤섞인다). 필드로 두면 전략이 몇 개든 안전하다.
        self.add_recording(Trade, alpha_sink, name="trade")
        self.add_recording(IndicatorSnapshot, alpha_sink, name="indicator")
        self.add_recording(events.Bar, alpha_sink, name="bar")
        self.add_recording(events.Tick, alpha_sink, name="tick")
        self.add_recording(events.Quote, alpha_sink, name="quote")
        # events.Notice — 실전(KiSEngine)/모의(SimBroker) 체결통보가 이제
        # 하나의 정규화된 타입이라 여기 한 번만 등록하면 둘 다 잡힌다.
        self.add_recording(events.Notice, alpha_sink, name="notice")

        # ── 콘솔 뷰 구독 — 등록 순서가 곧 'v' 화면의 숫자키(1,2,3...) ──
        #    add_view() 가 아니라 _add_default_view() 를 쓴다 — build_trader()
        #    에서 add_view() 로 등록하는 사용자 뷰(MACD 등)가 항상 이 뒤로
        #    가게 하려는 것이다(_apply_view() 참고).
        self._add_default_view(events.Tick, Board(cols=["dt", "price", "volume"]), name="시세판")
        self._add_default_view(events.Quote, Latest(), name="호가")
        self._add_default_view(events.Notice, Recent(100000, cols=["dt", "symbol", "order_no", "filled_qty", "price", "rejected"]), name="체결통보")
        self._add_default_view(events.Bar, Recent(20, cols=["dt", "symbol", "open", "high", "low", "close", "volume"]), name="봉")

        self._add_default_view(Trade, Recent(100000, cols=["strategy_id", "symbol", "size", "entry_dt", "entry_price", "exit_dt", "exit_price", "gross_pnl", "commission"]), name="거래(trade)")
        self._add_default_view(Fill, Recent(100000, cols=["dt", "symbol", "side", "size", "price", "order_id", "commission"]), name="체결(fill)")
        self._add_default_view(Position, Board(by="symbol", cols=["strategy_id", "size", "avg_price", "last_price"]), name="포지션")
        # price 는 주문 낼 때 지정한 값이라 시장가 주문은 애초에 None이다.
        # 실제로 체결된 가격은 avg_fill_price(Order.apply_fill()이 채움)에 있다 —
        # 이걸 안 보여주면 시장가 체결이 화면에서 전부 "-"로만 보인다.
        # render_interval=60 — 'oc 주문id'로 취소를 타이핑하는 동안 화면이
        # 지워지면 안 되는 패널은 이거 하나뿐이다. 나머지 패널은 안 줘서
        # 기본 1초 그대로 자동 갱신된다.
        self._add_default_view(Order, Board(by="id", cols=["strategy_id", "symbol", "side", "size", "filled_size", "price", "avg_fill_price", "status", "created_at", "updated_at"]), name="주문", render_interval=60.0)
        # AccountSnapshot — cash/equity 는 Broker.@property 라 자체 이벤트가
        # 없다. Trader.feed_timer(보통 1초 간격)가 찍어서 흘린다(trading.py 참고).
        self._add_default_view(AccountSnapshot, Board(by="strategy_id", cols=["dt", "cash", "equity"]), name="계좌")

        # 단일 라인 지표(SMA/RSI/ATR/CrossOver 등)는 전부 라인 이름을 "value"로
                # 통일해서 쓴다(indicators.py 관례) — 그래야 where={"line":"value"}
                # 하나로 다중 라인 지표(MACD 등)와 자동으로 갈린다. 안 걸러내면 MACD의
                # macd/signal/histo 가 label="MACD" 한 칸에서 서로 덮어써서 뒤섞인다.
                # MACD처럼 라인이 여럿인 지표의 전용 화면은 build_trader() 에서
                # (label을 아는 자리에서) 따로 등록한다.
        self._add_default_view(IndicatorSnapshot, Pivot(), name="지표", where={"line": "value"})

    # ── 레코더 구독 등록 ─────────────────────────────────────────
    def add_recording(self, dtype: type, sink, name: Optional[str] = None,
                       **kwargs) -> "AlphaTrader":
        """이 트레이더가 만드는 데이터(IndicatorSnapshot 등)를 무엇을
        어디에 저장할지 등록한다. add_strategy() 처럼 build_trader() 자리에서
        부르면 된다 — 등록만 쌓아두고, 실제 구독은 즉시 적용된다
        (Recorder.subscribe 는 스레드 기동과 무관하다).

        kwargs 는 Recorder.subscribe() 로 그대로 전달된다(batch, max_age, extra)."""
        self._recorder.subscribe(dtype, sink, name=name, **kwargs)
        log.info("레코더 구독 등록: %s → %s", dtype.__name__, name or dtype.__name__.lower())
        return self

    # ── 콘솔 뷰 구독 등록 ────────────────────────────────────────
    def add_view(self, dtype: type, agg: Aggregator, name: Optional[str] = None,
                 where=None, render_interval: Optional[float] = None) -> "AlphaTrader":
        """콘솔 뷰(구독 화면)에 패널 하나를 등록한다. 등록 순서가 곧
        숫자키(1,2,3...)다 — add_strategy() 와 나란히 build_trader() 자리에서
        부르면 된다. 실제 app.subscribe() 호출은 run_live/run_sim 이
        Application 을 만드는 시점에 이 목록을 그대로 재생한다.

        render_interval: 이 패널을 보는 동안 자동 새로고침 주기(초).
            안 주면 기본 1초로 그려진다. 명령을 길게 타이핑하는 패널
            (예: 'oc 주문id'로 취소하는 주문 패널)만 늘려서 준다 — 그래야
            타이핑 도중 화면이 안 지워진다. 나머지 패널은 손 안 대도
            1초 그대로다(FeedController.render_interval 참고)."""
        self._view_specs.append(dict(dtype=dtype, agg=agg, name=name, where=where,
                                     render_interval=render_interval))
        log.info("뷰 구독 등록: %s (%s)", name or dtype.__name__, type(agg).__name__)
        return self

    def _add_default_view(self, dtype: type, agg: Aggregator, name: Optional[str] = None,
                          where=None, render_interval: Optional[float] = None) -> None:
        """add_view() 와 같지만 _default_view_specs 에 쌓는다.
        _ensure_default_recording_and_views() 전용 — build_trader() 에서
        add_view() 로 등록한 사용자 뷰가 항상 뒤 번호를 받게 하려는 것이다."""
        self._default_view_specs.append(dict(dtype=dtype, agg=agg, name=name, where=where,
                                             render_interval=render_interval))
        log.info("뷰 구독 등록(기본): %s (%s)", name or dtype.__name__, type(agg).__name__)

    def _apply_view(self, app: Application) -> Application:
        # 기본 뷰가 먼저 숫자키를 차지하고, build_trader() 에서 add_view() 로
        # 등록한 사용자 뷰(MACD 등)는 그 뒤로 간다.
        for spec in self._default_view_specs + self._view_specs:
            app.subscribe(spec["dtype"], spec["agg"], name=spec["name"], where=spec["where"],
                          render_interval=spec["render_interval"])
        return app

    def _build_engine(self, broker, dry_run: bool, view_q=None) -> Engine:
        """실전/모의/백테스트가 공유하는 조립 지점.
        bt_broker.run_backtest 가 요구하는 build_engine(broker, dry_run) 시그니처와
        맞춰뒀다 — 백테스트 경로도 이 메서드를 그대로 넘겨 쓴다(view_q 는
        기본값 None 이라 위치 인자 두 개짜리 호출과 그대로 호환된다).

        view_q 를 여기서 Engine 에 심는 이유: Engine.feed()/feed_timer() 가
        만드는 봉과 흘러들어오는 외부 이벤트를 콘솔 뷰로 보내는 유일한
        통로가 Engine 이어야, LiveRunner 가 따로 view_q 에 넣던 것과 겹쳐
        화면에 같은 이벤트가 두 번 뜨는 일이 없다."""
        self._recorder.start()   # 이미 떠 있으면 no-op
        eng = Engine(real_broker=broker, dry_run=dry_run, state_dir=self.state_dir,
                    recorder=self._recorder, view_q=view_q)
        for spec in self._specs:
            eng.add(spec["strategy_id"], spec["strategy"],
                     allocation=spec["allocation"], ticks=spec["ticks"],
                     quotes=spec["quotes"], bars=spec["bars"],
                     warmup=spec["warmup"], history=spec["history"])
        return eng

    # ── 실행 경로 ────────────────────────────────────────────────
    def run_live(self, market_event_queue, dry_run: bool = True,
                 business_date: Optional[date] = None,
                 kill_file: str = "./STOP_TRADING",
                 on_quit: Optional[Callable[[], None]] = None,
                 ws=None) -> LiveRunner:
        """실계좌. market_event_queue 는 KiSEngine.market_event_queue —
        웹소켓 수신·파싱·정규화가 이미 끝난 MarketEvent/Notice 스트림이다.

        on_quit : 콘솔에서 'q'를 누르면 불린다. 데이터 소스(KiSEngine.stop)를
                  세우는 것까지만 책임진다 — 그래야 kis.run()의 블로킹이
                  풀리고, 다운스트림 드레인·flush는 호출자(메인 파일)가
                  kis.run() 리턴 뒤에 이어서 한다.
        ws      : KiSEngine.ws(KisFeed). 콘솔의 수동 주문 화면이 '구독 중인
                  종목' 번호 목록을 보여주는 데 쓴다 — 없어도 동작하지만
                  그 화면에서 번호 목록이 비어 보인다."""
        log.info("AlphaTrader.run_live 시작 — 전략 %d개, dry_run=%s", len(self._specs), dry_run)
        broker = KISBroker()
        self._ensure_manual_strategy()
        self._ensure_default_recording_and_views(simul_mode=False)

        # Application(view_q)을 Engine보다 먼저 만든다 — Engine.feed()가
        # 만드는 봉/외부이벤트를 view_q로 바로 흘리려면 Engine 생성 시점에
        # 그 큐가 이미 있어야 한다.
        app = self._apply_view(Application(on_quit=on_quit, ws=ws))
        eng = self._build_engine(broker, dry_run, view_q=app.view_q)
        # 콘솔 수동 주문 화면이 engine.slots[...] 로 StrategyBroker를 찾아야
        # 해서, Engine이 만들어진 뒤에 ctx에 심는다(Application보다 늦게
        # 생기므로 생성자에서 바로 못 준다).
        app.ctx.engine = eng

        runner = LiveRunner(engine=eng, trading_q=market_event_queue, view_q=app.view_q, broker=broker,
                            test_mode=False, dry_run=dry_run,
                            business_date=business_date, kill_file=kill_file)
        runner.start()
        app.run()
        log.info("AlphaTrader.run_live — LiveRunner 기동 완료")
        return runner

    def run_sim(self, market_event_queue, cash: float = 100_000_000_000,
                business_date: Optional[date] = None,
                kill_file: str = "./STOP_TRADING",
                on_quit: Optional[Callable[[], None]] = None,
                ws=None,
                simul_mode: bool = False,
                **sim_broker_kwargs) -> LiveRunner:
        """모의투자. 실시간 market_event_queue 로 시세를 받되 주문은 가짜다.

        ★ SimBroker 가 같은 큐에 체결통보를 되돌려 넣는다 ★
          fill_q=market_event_queue 로 넘기면 시세와 모의체결이 한 큐에서
          순서대로 나온다 — LiveRunner 가 실전과 똑같은 소비 루프로 처리한다.

        on_quit    : run_live 와 같다 — 콘솔 'q'는 데이터 소스만 세운다.
        ws         : run_live 와 같다 — 수동 주문 화면의 종목 번호 목록용.
        simul_mode : True면 alpha_data_sim.db(테스트용 fake 피드), False면
                     alpha_data.db — 호출자가 KiSEngine(simul_mode=...)에
                     준 값과 같은 값을 그대로 줄 것(레코딩 파일이 갈린다)."""
        log.info("AlphaTrader.run_sim 시작 — 전략 %d개, cash=%s", len(self._specs), cash)
        broker = SimBroker(fill_q=market_event_queue, cash=cash, **sim_broker_kwargs)
        self._ensure_manual_strategy()
        self._ensure_default_recording_and_views(simul_mode=simul_mode)
        app = self._apply_view(Application(on_quit=on_quit, ws=ws))
        eng = self._build_engine(broker, dry_run=False, view_q=app.view_q)   # 모의는 항상 가짜주문을 낸다
        app.ctx.engine = eng
        runner = LiveRunner(engine=eng, trading_q=market_event_queue, view_q=app.view_q, broker=broker,
                            test_mode=True, dry_run=False,
                            business_date=business_date, kill_file=kill_file)
        runner.start()
        app.run()
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
        # 백테스트엔 kis_data_sim.db 개념이 없다 — alpha_data.db 고정.
        self._ensure_default_recording_and_views(simul_mode=False)
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
