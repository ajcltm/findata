import asyncio
import threading
import queue

from kis import kis_websocket
from kis import kis_simulator
from kis import kis_parser
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
        self.consumer_queue = {'recording_q': queue.Queue(), 'trading_q': queue.Queue(), 'show_q': queue.Queue()}

        self.ws = kis_websocket.KisFeed(price_codes=self.price_codes, orderbook_codes=self.orderbook_codes, consumer_queues=self.consumer_queue, simul_mode=self.simul_mode)
        self.parser = kis_parser.KISParser()

        self._stop = threading.Event()
        self.strategy = None

    def add_strategy(self, strategy):
        self.strategy = strategy
        self.logger.info("strategy added.")

    def start_recording(self):
        recorder = kis_recorder.KisRecorder(price_codes=self.price_codes, orderbook_codes=self.orderbook_codes, save_q=self.consumer_queue['recording_q'], stop_event=self._stop, parser=self.parser, test_mode = self.simul_mode)
        threading.Thread(target=recorder.recording, daemon=True).start()
        self.logger.info("recording thread on.")

    def start_trading(self):
        trader = kis_trader.KisTrader(strategy = self.strategy, trading_q=self.consumer_queue['trading_q'], parser=self.parser, stop_event = self._stop, test_mode = self.simul_mode)
        threading.Thread(target=trader.trading, daemon=True).start()
        self.logger.info("trading thread on.")

    def start_showing(self):
        ui = kis_controller.ScreenManager(event_q=self.consumer_queue['show_q'], parser = self.parser, subscriptions=self.ws._ack)
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

        asyncio.run(self.ws.run())
