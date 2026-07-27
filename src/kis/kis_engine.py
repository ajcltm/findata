import asyncio
import threading

from kis import kis_websocket_
from kis import kis_simulator
from kis import kis_parser
from kis import kis_queue_bridge
from kis import kis_recorder
from kis import kis_trader
from src.kis.mvc import kis_controller

import logging

class KiSEngine:
    def __init__(self, price_codes=None, orderbook_codes=None, simul_mode=False):

        self.logger = logging.getLogger("kis")
        self.simul_mode = simul_mode

        self.price_codes = price_codes
        self.orderbook_codes = orderbook_codes

        self.ws = kis_websocket_.KISWebSocket(price_codes=self.price_codes, orderbook_codes=self.orderbook_codes, simul_mode=self.simul_mode)
        self.parser = kis_parser.KISParser(self.ws.crypto_info)

        self.recording_bridge = kis_queue_bridge.KisQueueBridge()
        self.trading_bridge = kis_queue_bridge.KisQueueBridge()
        self.showing_bridge = kis_queue_bridge.KisQueueBridge()

        self._stop = threading.Event()
        self.strategy = None

    def add_strategy(self, strategy):
        self.strategy = strategy
        self.logger.info("strategy added.")

    def start_recording(self):
        recorder = kis_recorder.KisRecorder(price_codes=self.price_codes, orderbook_codes=self.orderbook_codes, save_q=self.recording_bridge.thread_q, stop_event=self._stop, parser=self.parser, test_mode = self.simul_mode)
        threading.Thread(target=recorder.recording, daemon=True).start()
        self.logger.info("recording thread on.")

    def start_trading(self):
        trader = kis_trader.KisTrader(strategy = self.strategy, trading_q=self.trading_bridge.thread_q, parser=self.parser, stop_event = self._stop, test_mode = self.simul_mode)
        threading.Thread(target=trader.trading, daemon=True).start()
        self.logger.info("trading thread on.")

    def start_showing(self):
        ui = kis_controller.ScreenManager(event_q=self.showing_bridge.thread_q, parser = self.parser, subscriptions=self.ws.subscriptions)
        threading.Thread(target=ui.start, daemon=True).start()
        self.logger.info("showing thread on.")

    def run(self, recording=True, trading=False, show=True):
        self.logger.info("websocket mode run.")
        
        if recording:
            self.start_recording()

        if trading :
            self.start_trading()

        if show:
            self.start_showing()

        asyncio.run(self._run())

    async def _run(self):
        
        await asyncio.gather(
            self.ws.run(),
            self.recording_bridge.run(self.ws.recording_q),
            self.trading_bridge.run(self.ws.trading_q),
            self.showing_bridge.run(self.ws.show_q)
        )
