# src 재분류안 (N-002)

## 1. 새 폴더 트리

```
src/
├── alpha/
│   ├── __main__.py
│   ├── alphatrader/
│   │   └── alphatrader.py
│   ├── analysist/
│   │   ├── __init__.py
│   │   ├── finance.py
│   │   ├── plotter.py
│   │   ├── tdata.py
│   │   └── datastore/
│   │       ├── __init__.py
│   │       ├── base.py
│   │       ├── fdr.py
│   │       └── local.py
│   ├── core/                      (새 폴더)
│   │   ├── orders.py              (새 파일)
│   │   └── trades.py              (새 파일)
│   ├── broker/
│   │   ├── base.py                (새 파일)
│   │   ├── bt_broker.py
│   │   ├── kis_broker.py
│   │   ├── portfolio_broker.py
│   │   └── sim_broker.py
│   ├── engine/
│   │   ├── backtest.py            (새 파일)
│   │   ├── engine.py
│   │   ├── live_runner.py
│   │   └── universe.py
│   ├── events/
│   │   ├── events.py
│   │   └── normalize.py           (새 파일)
│   ├── indicators/
│   │   ├── indicators.py
│   │   ├── kalman.py
│   │   └── microstructure.py
│   ├── recording/
│   │   ├── recorder.py
│   │   ├── records.py             (새 파일)
│   │   └── sinks.py
│   ├── strategy/
│   │   ├── base.py                (새 파일)
│   │   ├── dobi_watcher.py
│   │   ├── indicator_watcher.py
│   │   ├── manual.py
│   │   ├── microstructure_watcher.py
│   │   ├── sma_cross_atr.py
│   │   └── spread_watcher.py
│   ├── trader/
│   │   ├── killswitch.py
│   │   └── trader.py              (새 파일. trading.py는 없어진다)
│   └── view/
│       ├── app.py
│       ├── controller.py
│       ├── model.py
│       └── view.py
├── kis/
│   ├── __init__.py
│   ├── fake_kis_websocket.py
│   ├── kis_api.py
│   ├── kis_config.py
│   ├── kis_engine.py
│   ├── kis_logger.py
│   ├── kis_parser.py
│   ├── kis_tocken.py
│   └── kis_websocket.py
└── legacy/                        (새 폴더)
    ├── config_log.py
    ├── kis_crypto.py
    ├── kis_main.py
    ├── kis_simulator.py
    ├── microstructure_kalman.py
    ├── order.py
    ├── overnight.py
    └── test_st.py
```

- 없어지는 폴더: `alpha/order/`, `alpha/config/`
- 없어지는 파일: `alpha/trader/trading.py`

## 2. 옮길 목록

| # | 옛 위치 (파일:클래스/함수) | 새 위치 (파일) | 이유 |
| --- | --- | --- | --- |
| 1 | `alpha/trader/trading.py`: `EPS`, `Side`, `OrderType`, `OrderStatus`, `TERMINAL`, `Order`, `Position`, `Fill` | `alpha/core/orders.py` | 공통 값 객체 |
| 2 | `alpha/trader/trading.py`: `Trade`, `_OpenTrade`, `TradeTracker` | `alpha/core/trades.py` | 거래 조립 |
| 3 | `alpha/trader/trading.py`: `IndicatorSnapshot`, `AccountSnapshot`, `StrategySpec` | `alpha/recording/records.py` | 기록용 행 |
| 4 | `alpha/trader/trading.py`: `Broker`, `GuardBroker` | `alpha/broker/base.py` | 브로커 기반 |
| 5 | `alpha/trader/trading.py`: `_IndSlot`, `Strategy` | `alpha/strategy/base.py` | 전략 기반 |
| 6 | `alpha/trader/trading.py`: `Trader` | `alpha/trader/trader.py` | 이름 맞춤 |
| 7 | `alpha/broker/bt_broker.py`: `round_to_tick` | `alpha/broker/base.py` | 중복 3개 통합 |
| 8 | `alpha/broker/kis_broker.py`: `round_to_tick` | `alpha/broker/base.py` | 중복 3개 통합 |
| 9 | `alpha/broker/sim_broker.py`: `round_to_tick` | `alpha/broker/base.py` | 중복 3개 통합 |
| 10 | `alpha/broker/bt_broker.py`: `_Pump`, `run_backtest` | `alpha/engine/backtest.py` | 실행 루프 |
| 11 | `alpha/events/events.py`: `_f`, `_fo`, `_parse_recv`, `parse_hhmmss`, `from_execution`, `from_orderbook`, `from_notice` | `alpha/events/normalize.py` | 원본→이벤트 |
| 12 | `alpha/indicators/microstructure.py`: `KalmanTrend` | `legacy/microstructure_kalman.py` | kalman.py가 대체 |
| 13 | `alpha/order/order.py`: `now_iso`, `OrderStatus`, `Order` | `legacy/order.py` | import 0건 |
| 14 | `alpha/config/log.py`: `setup_logger` | `legacy/config_log.py` | import 0건 |
| 15 | `alpha/strategy/overnight.py`: `OvernightMomentumStrategy` | `legacy/overnight.py` | import 0건 |
| 16 | `alpha/strategy/test_st.py`: 클래스 4개 전부 | `legacy/test_st.py` | import 0건 |
| 17 | `kis/kis_simulator.py`: `KISWebSocket` | `legacy/kis_simulator.py` | import 0건 |
| 18 | `kis/kis_crypto.py`: `aes256_cbc_base64_decrypt` | `legacy/kis_crypto.py` | import 0건 |
| 19 | `kis/__main__.py`: 파일 전체 | `legacy/kis_main.py` | 없는 모듈 import |

결정: 1A 2A 3A, records는 recording/
