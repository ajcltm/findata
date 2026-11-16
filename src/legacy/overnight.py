import backtrader as bt
import math

class OvernightMomentumStrategy(bt.Strategy):
    params = dict(
        hold_minutes="09:05",
        cash_buffer=0.001,   # 0.1% 현금 남겨 주문 거절(Margin) 방지
    )

    def __init__(self):
        self.entry_dt = {d: None for d in self.datas}
        self.ordered  = {d: False for d in self.datas}

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

    def next(self):
        
        for d in self.datas:
            dt = d.datetime.datetime()
            hhmm = dt.strftime("%H:%M")
            pos = self.getposition(d).size
            
            if dt.strftime("%H:%M") == "09:00" and not self.ordered[d]:
                size = self._calc_all_in_size(d)
                if size > 0:
                    self.ordered[d] = True
                    self.buy(data=d, size=size)
                    print(f"BUY  {d._name} dt={dt} size={size} cash={self.broker.getcash():,.0f}")
                else:
                    print(f"SKIP {d._name} dt={dt} (size=0, cash={self.broker.getcash():,.0f}, px={float(d.close[0]):,.0f})")
            if self.entry_dt[d] is not None and pos > 0 and (not self.ordered[d]) and hhmm == self.p.hold_minutes:
                self.ordered[d] = True
                self.sell(data=d, size=pos)
                print(f"SELL {d._name} dt={dt} size={pos}")