import datetime
from kis import kis_api
import backtrader as bt
import math
from types import SimpleNamespace
import logging

class test_strategy:

    def __init__(self, data, test_mode):
        self.data = data
        self.test_mode = test_mode

        self.logger = logging.getLogger("trading")
        

    def next(self):
        self.logger.info(f"test_strategy.next() called with data size={len(self.data)}")
        self.logger.info(f"test_strategy.next() called with data : {self.data}")
        self.logger.info(f"test ?? => {self.test_mode}")


class kis_test_strategy:

    def __init__(self, data, price_book=None, order_book=None):
        self.data = data
        self.price_book = price_book
        self.order_book = order_book

    def next(self):
        now = datetime.datetime.now()

        if now.strftime("%H:%M") == "09:00" :
            print(f"{now.strftime('%H:%M:%S')} 매수 주문 시간 입니다!")
            for stock in self.price_book :
                if self.ordered.get(stock) is False:
                    self.ordered[stock] = True
                    r= kis_api.buy(code=stock, qty="1")
                    print(f"{now} {stock} : 매수 주문 들어갔음!")
                    print(r)

        if now >= datetime.datetime(2026, 2, 20, 9, 5) :
            print(f"{now.strftime('%H:%M:%S')} 매도 주문 시간 입니다!")
            for stock in self.holdpty :
                holdqty = self.holdpty.get(stock)
                if holdqty != 0:
                    # self.ordered[stock] = False
                    self.holdpty[stock] = 0
                    r= kis_api.sell(code=stock, qty=holdqty)
                    print(r)


class OvernightMomentumStrategy():
    params = dict(
        buy_minutes="15:23",
        sell_minutes="15:24",
        cash_buffer=0.001,   # 0.1% 현금 남겨 주문 거절(Margin) 방지
    )

    def __init__(self):
        self.target_codes = [d._name for d in self.datas]
        self.ordered  = {d._name: False for d in self.datas}
        self.data_map = {d._name: d for d in self.datas}
        self.entry_dt = {d._name: None for d in self.datas}
        self.logger = logging.getLogger("trading")
        
        

    def update_stdata(self):
        pass

    def notify_order(self, order):
        print(f"Order Notification: {order.Status[order.status]} for {order.data._name} at {self.data.datetime.datetime()}")

    def _calc_all_in_size(self, d):
        """현재 가용현금으로 살 수 있는 최대 수량 계산"""
        cash = self.broker.getcash()

        # 시장가 근사 가격: close(또는 open으로 바꿔도 됨)
        px = float(d.close[0])

        if px <= 0:
            return 0

        # 수수료 고려 (설정 안 했으면 commission=0)
        comminfo = self.broker.getcommissioninfo(d)
        cost_per_share = px * (1.0 + comminfo.p.commission)

        usable_cash = cash * (1.0 - self.p.cash_buffer)
        size = int(math.floor(usable_cash / cost_per_share))
        return max(size, 0)
    
    def get_current_datetime(self, d):
        """데이터 d의 현재 바의 datetime을 반환하는 헬퍼 함수"""
        return d.datetime.datetime()
    
    def get_position_size(self, d):
        """데이터 d의 현재 포지션 수량을 반환하는 헬퍼 함수"""
        return self.getposition(d).size
    
    def st_buy(self, d, size):
        """d 종목을 size 수량만큼 매수하는 헬퍼 함수"""
        self.buy(data=d, size=size)

    def st_sell(self, d, size):
        """d 종목을 size 수량만큼 매도하는 헬퍼 함수"""
        self.sell(data=d, size=size)

    def next(self):
        
        for d in self.target_codes:
            dt = self.get_current_datetime(self.data_map[d])
            hhmm = dt.strftime("%H:%M")
            pos = self.get_position_size(self.data_map[d])


            if dt.strftime("%H:%M") == self.p.buy_minutes and not self.ordered[d]:
                size = self._calc_all_in_size(self.data_map[d])
                if size > 0:
                    self.ordered[d] = True
                    self.entry_dt[d] = dt
                    self.st_buy(self.data_map[d], size)
                else:
                    continue

            if d is not None and pos > 0 and (self.ordered[d]) and hhmm == self.p.sell_minutes:
                self.ordered[d] = False
                self.st_sell(self.data_map[d], pos)

class KisOvernightMomentumStrategy(OvernightMomentumStrategy):

    def __init__(self, data, test_mode):
        self.target_codes = [d for d in ["263750", "009830", "047040"]]
        self.ordered  = {d: False for d in self.target_codes}
        self.data_map = {d: d for d in self.target_codes}
        self.entry_dt = {d: None for d in self.target_codes}
        self.position = {d: 0 for d in self.target_codes}
        self.data = data
        self.test_mode = test_mode
        self.p = SimpleNamespace(**self.params)  # 딕셔너리를 네임스페이스로 변환하여 self.params로 접근 가능하게 함
        self.logger = logging.getLogger("trading")

    def update_stdata(self):
        pass

    def _calc_all_in_size(self, d):
        """현재 가용현금으로 살 수 있는 최대 수량 계산"""
        return 1
    
    def get_current_datetime(self, d):
        """데이터 d의 현재 바의 datetime을 반환하는 헬퍼 함수"""
        return datetime.datetime.now()
    
    def get_position_size(self, d):
        """데이터 d의 현재 포지션 수량을 반환하는 헬퍼 함수"""
        return self.position[d]
    
    def st_buy(self, d, size):
        """d 종목을 size 수량만큼 매수하는 헬퍼 함수"""
        r = kis_api.buy(code=d, qty=str(size))
        self.logger.info(f"buy try result : {r}")
        self.position[d] += size
        self.logger.info(f"buy : code : {d}, ordered : {self.ordered}, position: {size}, entry_dt: {self.entry_dt}")

    def st_sell(self, d, size):
        """d 종목을 size 수량만큼 매도하는 헬퍼 함수"""
        r = kis_api.sell(code=d, qty=str(size))
        self.logger.info(f"sell try result : {r}")
        self.position[d] -= size
        self.logger.info(f"sells : code : {d}, ordered : {self.ordered}, position: {size}, entry_dt: {self.entry_dt}")