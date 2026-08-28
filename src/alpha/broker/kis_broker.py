"""
kis_broker.py — 실전 매매용 Broker 구현체. backtrader import 없음.

■ 이 파일이 답하는 질문 하나
    "이 계좌, 지금 어떤 상태야?"

    backtrader는 물어보면 즉시 정답을 준다. 단일 프로세스 안의 시뮬레이션이니까.
    KIS는 아니다. 계좌의 진짜 상태는 증권사 서버에 있고, 우리는 두 경로로만 안다.

        느린 경로 : sync_balance()   REST 폴링. 정답이지만 몇 초 늦다.
        빠른 경로 : on_execution_report(), on_price()   웹소켓. 즉시지만 추정치.

    그래서 로컬에 상태를 '복제'해두고, 빠른 경로로 임시 반영한 뒤
    느린 경로가 주기적으로 덮어쓴다. 이 파일 코드의 절반이 이 이중 구조 때문에 있다.

■ bt_broker.py 와 대칭 구조
        BacktraderBroker  ↔  KISBroker    (Broker 인터페이스 구현체)
        _Pump             ↔  EngineTrader (외부 이벤트를 Engine 으로 밀어넣는 쪽)

■ 백테스트와 다른 지점 세 곳 — 전부 이 파일에서 흡수한다
    ① 잔고가 실시간이 아니다      → _reserved 로 낙관적 차감
    ② 봉을 내가 만들어야 한다      → engine.BarFactory (틱을 봉으로 집계)
    ③ 체결이 비동기다             → on_execution_report 콜백
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

# 공통 계층에서 가져온다. 이 파일은 trading.py 에만 의존한다.
from alpha.trader.trading import (Broker, Fill, Order, OrderStatus, OrderType,
                     Position, Side, Strategy, Trader)
from kis import kis_api

log = logging.getLogger(__name__)


def round_to_tick(price: float) -> float:
    """한국 주식 호가단위로 가격을 반올림한다.

    거래소는 아무 가격이나 받지 않는다. 예를 들어 3만원대 종목은 50원 단위로만
    주문할 수 있어서 30,123원 같은 지정가는 거부된다.

    주의: 이 함수는 백테스트(bt_broker.py)에도 똑같이 들어가 있다.
          백테스트만 자유로운 가격으로 체결시키면 실전보다 성과가 좋게 나온다.
    """
    p = float(price)
    # (가격 상한, 그 구간의 호가단위) 쌍을 낮은 가격부터 훑는다.
    for limit, tick in ((2000, 1), (5000, 5), (20000, 10),
                        (50000, 50), (200000, 100), (500000, 500)):
        if p < limit:
            # tick 으로 나눠 반올림한 뒤 다시 곱하면 tick 배수가 된다.
            return round(p / tick) * tick
    return round(p / 1000) * 1000        # 50만원 이상은 1,000원 단위


# ══════════════════════════════════════════════════════════════════
# KISBroker — Broker 추상 클래스의 실전 구현체
# ══════════════════════════════════════════════════════════════════

class KISBroker(Broker):
    """Broker ABC 가 요구하는 7개(cash/equity/now/position/open_orders/
    submit/cancel)만 채우면 buy/sell/close/target_pct 는 베이스가 만들어준다.

    REST 호출은 self.api 가 아니라 kis_api 모듈 함수를 직접 부른다
    (kis_api.place_order, kis_api.balance, kis_api.cancelable_orders/
    domestic_stock_order_cancel 등) — submit()/sync_balance()/cancel() 참고.
    이 클래스는 api 객체를 주입받지 않는다(생성자가 인자를 안 받는다).

    ⚠ 실거래 투입 전 확인할 것 — 모의투자 계좌로 주문·취소를 실제로 태워
      KIS 응답의 정확한 필드명(ODNO vs ORD_NO 등, submit() 참고)과
      RVSE_CNCL_DVSN_CD/QTY_ALL_ORD_YN 동작을 검증하지 못했다."""

    def __init__(self):

        # ── 계좌 상태의 로컬 복제본 ──────────────────────────
        # 전부 '증권사 서버 상태의 그림자'다. 원본은 저쪽에 있다.
        self._cash = 0.0                            # 예수금. sync_balance 가 채움
        self._equity = 0.0                          # 총평가금액. target_pct 의 분모
        self._positions: dict[str, Position] = {}   # 종목별 보유 현황 + 현재가

        # ── 주문 장부를 두 벌 유지하는 이유 ──────────────────
        # 우리는 Order.id("o000001")로 주문을 식별하는데,
        # 체결통보 웹소켓은 KIS 주문번호로 온다. 역방향 조회가 필요하다.
        self._orders: dict[str, Order] = {}         # 우리 id  -> Order
        self._by_broker_id: dict[str, Order] = {}   # KIS 번호 -> Order

        # ── 차이 ① 대응 ────────────────────────────────────
        # 미체결 매수 주문에 묶인 금액. 아래 cash 프로퍼티 주석 참조.
        self._reserved = 0.0

        # ── 스레드 안전 ────────────────────────────────────
        # 이 객체는 최소 3개 스레드가 동시에 건드린다.
        #   전략 스레드 : position(), submit()      읽고 쓴다
        #   소켓 스레드 : on_execution_report()     쓴다
        #   폴링 스레드 : sync_balance()            쓴다
        # RLock 인 이유: submit()이 락을 쥔 채 락을 쓰는 다른 메서드를 부를 수
        # 있는데, 일반 Lock 이면 자기 자신에게 걸려서 교착된다.
        # (backtrader 는 단일 스레드라 이 개념 자체가 없다)
        self._lock = threading.RLock()

        # ── 시계 ──────────────────────────────────────────
        # datetime.now() 를 코드 여기저기서 부르지 않는다. now 프로퍼티 참조.
        self._now = datetime.now()

    # ══════════════════════════════════════════════════════
    # 차이 ① — 잔고가 실시간이 아니다
    #
    # backtrader: buy() 를 부르는 즉시 broker.cash 가 줄어든다.
    # KIS       : 주문을 내도 예수금은 그대로다. 폴링이 돌 때까지 몇 초.
    #
    # 그 사이 전략이 target_pct 를 또 계산하면 "아직 돈이 다 있네"라고 판단해
    # 같은 돈으로 두 번 산다. 그래서 미체결 매수 금액을 미리 빼둔다.
    # ══════════════════════════════════════════════════════
    @property
    def cash(self) -> float:
        """전략이 보는 '쓸 수 있는 돈'. 실제 예수금에서 예약금을 뺀 값."""
        return self._cash - self._reserved

    @property
    def equity(self) -> float:
        """총평가금액(예수금 + 보유종목 평가액). target_pct 의 분모다.
        폴링으로만 갱신되므로 시세가 급변하면 잠시 낡은 값이 된다."""
        return self._equity

    @property
    def now(self) -> datetime:
        """현재 시각. datetime.now() 를 직접 쓰지 않는 게 핵심이다.

        백테스트는 '봉의 시각'이 곧 현재이고, 실전은 '틱의 시각'이 현재다.
        양쪽이 broker.now 라는 같은 창구로 시간을 얻어야
        전략 코드에 datetime.now() 가 새어들지 않는다.
        (전략에 실제 현재시각이 들어가면 백테스트가 미래를 보게 된다)

        실전에서는 Engine.feed → on_market 이 틱마다 _now 를 갱신해준다."""
        return self._now

    # ---------- 조회 ----------
    def position(self, symbol: str) -> Position:
        """보유 현황. 없으면 None 이 아니라 size=0 인 빈 Position 을 준다.

        이렇게 하면 전략이 `if pos is None or pos.size == 0` 같은 걸 안 써도
        `if pos.is_flat` 하나로 끝난다. (Null Object 패턴)"""
        with self._lock:
            return self._positions.get(symbol, Position(symbol=symbol))

    def open_orders(self, symbol=None) -> list[Order]:
        """아직 살아있는(체결/취소/거부되지 않은) 주문 목록.
        symbol=None 이면 전 종목. Broker.has_pending() 이 이걸 쓴다."""
        with self._lock:
            return [o for o in self._orders.values()
                    if o.is_alive and (symbol is None or o.symbol == symbol)]

    # ---------- 주문 ----------
    def submit(self, order: Order) -> Order:
        """실제 REST 전송. Broker._order() 가 가드를 통과시킨 뒤 부른다.

        ■ 계약 (BacktraderBroker.submit 과 동일해야 함)
            성공 → status = SUBMITTED, broker_id 설정
            실패 → status = REJECTED, reject_reason 설정
                   ★ 예외를 밖으로 던지지 않는다 ★
        """

        # ── 1단계: 재전송 방어 ─────────────────────────────
        # 같은 Order 객체가 두 번 들어오는 경우(재시도 로직 등)를 막는다.
        # 서로 다른 신호로 인한 중복 주문은 Broker._order() 의
        # has_pending 가드가 이미 걸렀다 — 여기는 마지막 방어선.
        #
        # 주문 ID를 KIS가 아니라 '우리가' 만드는 이유가 이것이다.
        # 증권사가 번호를 주기 전에 이미 식별자가 있어야 검사할 수 있다.
        with self._lock:
            prev = self._orders.get(order.id)
            if prev is not None and prev.is_alive:
                return prev                  # 이미 살아있는 같은 주문 → 무시
            self._orders[order.id] = order   # 장부에 먼저 올린다

        # ── 2단계: 실제 REST 전송 ──────────────────────────
        # 락을 놓고 호출한다. 네트워크 I/O 는 수백 ms 가 걸릴 수 있는데
        # 그동안 락을 쥐고 있으면 소켓 스레드가 통째로 멈춘다.
        try:
            response = kis_api.place_order(
                symbol=order.symbol,
                side=order.side.value,                          # "buy" / "sell"
                qty=int(order.size),                            # KIS는 정수 수량만
                price=int(order.price) if order.price else 0,   # 0 = 시장가
            )

            result = response.get("rt_cd")
            # ⚠ 필드명 확인 필요: KIS의 다른 주문 관련 응답(정정취소,
            # 정정취소가능조회)은 전부 "ODNO"를 쓴다(kis_api.py 의
            # domestic_stock_order_cancel/cancelable_orders 참고).
            # 취소 기능이 order.broker_id(=여기서 뽑은 값)로 원주문을
            # 찾으므로, 응답에 "ORD_NO"가 없으면 broker_id 가 계속 "None"
            # 문자열이 되어 취소·체결통보 매칭이 전부 깨진다. 모의투자로
            # 실제 응답 바디를 한 번 찍어 어느 키가 맞는지 확인할 것.
            output = response.get("output", {}) or {}
            broker_id = output.get("ODNO") or output.get("ORD_NO")
            log.info("주문 %s ", order)

            if result != "0" :
                # KIS는 주문이 거부돼도 HTTP 200을 준다. 그래서 여기서 거부를
                # 감지해야 한다. (거부 사유는 response["msg1"]에 있다)
                order.status = OrderStatus.REJECTED
                order.reject_reason = response.get("msg1", "거부")
                log.warning("주문 거부 %s %s", order.id, order.reject_reason)
                return order
        
        except Exception as e:
            # 네트워크 실패·인증 만료·거래소 거부 — 전부 여기로 온다.
            # 예외를 위로 던지지 않는 게 중요하다. 전략은 "거부됐다"만 알면 되고
            # 엔진은 계속 살아있어야 한다.
            order.status = OrderStatus.REJECTED
            order.reject_reason = str(e)
            log.exception("주문 전송 실패 %s", order.id)
            return order

        # ── 3단계: 전송 성공 후 뒷정리 ─────────────────────
        with self._lock:
            order.broker_id = str(broker_id)
            order.status = OrderStatus.SUBMITTED
            # 여기서 역방향 매핑을 걸어야 나중에 체결통보를 받을 수 있다.
            # 이 줄이 빠지면 체결이 와도 어느 주문인지 몰라서 버려진다.
            self._by_broker_id[order.broker_id] = order

            # 매수 예약금 확보 (차이 ① 대응)
            # 알려진 문제: 시장가는 order.price 가 None 이라 여기 안 잡힌다.
            #             현재 전략이 전부 시장가면 _reserved 가 사실상 무력화된다.
            #             필요하면 last_price 로 추정치를 넣을 것.
            if order.side is Side.BUY and order.price:
                self._reserved += order.size * order.price

        log.info("주문 %s %s %s주 @ %s → %s", order.symbol, order.side.value,
                 order.size, order.price or "시장가", broker_id)
        return order

    def cancel(self, order: Order) -> None:
        """미체결 주문 취소. 이미 끝난 주문이면 조용히 무시한다.

        ★ REST 취소 응답만으로 낙관적으로 CANCELED 를 세팅한다 ★
          원래는 "상태는 여기서 안 바꾼다, 확정은 체결통보 웹소켓의
          on_execution_report(status="cancel") 에서 한다"였다. 그런데
          live_runner._handle_notice() 는 통보를 reject/fill 두 가지로만
          나누고 cancel 은 만들지 않는다 — H0STCNI0 은 취소 접수도 같은
          "1"(주문/정정/취소/거부 접수) 값으로 오는데, 그걸 신규주문
          접수와 구분할 근거(RCTF_CLS 등)를 KIS 공식 문서로 확정할 수가
          없어서, 실제로는 취소 확정이 영원히 안 와서 취소 요청만 뜨고
          주문 상태가 그대로 남는 버그가 있었다.

          submit() 이 REST 응답만으로 SUBMITTED 를 낙관적으로 세팅하는
          것과 같은 패턴으로, 여기서도 거래소가 취소를 접수했다는 REST
          응답(rt_cd=="0")만으로 CANCELED 를 세팅한다. 혹시 그 사이 실제로
          체결됐다면, 뒤이어 오는 진짜 체결통보의 apply_fill() 이 현재
          status 를 안 보고 무조건 FILLED/PARTIAL 로 덮어쓰므로 자동으로
          바로잡힌다 — 완전히 안전하지는 않지만(체결과 취소 사이의 아주
          짧은 경합), 지금까지처럼 취소가 영원히 안 먹히는 것보다 낫다.

        ★ self.api가 아니라 kis_api 모듈 함수를 직접 부른다 ★
          이 클래스는 api 객체를 주입받지 않는다(생성자가 인자를 안 받음).
          예전 코드는 존재하지 않는 self.api.cancel_order(...)를 불러서
          취소를 시도할 때마다 AttributeError로 조용히 실패하고 있었다.

        ★ 취소 직전에 KRX_FWDG_ORD_ORGNO를 다시 조회하는 이유 ★
          원주문 접수 응답에서 그 값을 안 받아 저장해두고 있고, 설령
          저장해뒀어도 재시작하면 로컬엔 없다. kis_api.cancelable_orders()
          (정정취소가능주문조회)가 지금 취소 가능한 주문들의 그 값을
          다시 준다 — 동시에 '이미 체결/취소돼서 더 이상 취소할 수
          없다'도 여기서 자연히 걸러진다."""
        if not order.broker_id or order.is_done:
            return
        try:
            cancelable = kis_api.cancelable_orders()
            info = cancelable.get(order.broker_id)
            if info is None:
                log.warning("취소 가능 목록에 없음(이미 체결/취소됐을 수 있음): %s(%s)",
                           order.id, order.broker_id)
                return
            r = kis_api.domestic_stock_order_cancel(
                krx_fwdg_ord_orgno=info["krx_fwdg_ord_orgno"],
                orgn_odno=order.broker_id)
            if r.get("rt_cd") != "0":
                log.warning("취소 실패 %s: %s", order.id, r.get("msg1"))
            else:
                log.info("취소 요청 전송 %s", order.id)
                with self._lock:
                    order.status = OrderStatus.CANCELED
                    order.updated_at = datetime.now()
        except Exception:
            log.exception("취소 실패 %s", order.id)

    # modify() 는 오버라이드하지 않는다 — Broker 베이스의 '취소 후 재주문'을 쓴다.
    #
    # ★ NotImplementedError 로 막으면 안 된다 ★
    #   백테스트에서 되던 게 실전에서만 터지면 이 구조를 쓰는 의미가 없다.
    #   "백테스트에 있는 기능은 실전에도 반드시 있다"가 지켜져야 한다.
    #
    # 개선 여지: KIS 는 정정주문 API 가 따로 있다. 취소-재주문은 호가 대기열
    # 맨 뒤로 밀리지만 정정은 순위를 유지하므로, 지정가 전략을 쓸 때
    # api.amend_order 를 연결해 오버라이드하면 체결률이 올라간다.

    # ---------- 시장 규칙 ----------
    # 백테스트도 똑같은 규칙을 써야 결과가 맞는다. bt_broker.py 참조.
    def round_price(self, price): return round_to_tick(price)
    def round_size(self, size): return float(int(size))   # 주식은 소수점 불가
    def min_size(self): return 1.0                        # 최소 1주

    # ══════════════════════════════════════════════════════
    # 외부(폴링·웹소켓)가 상태를 밀어넣는 메서드들
    # 위쪽은 전략이 부르는 쪽, 여기부터는 인프라가 부르는 쪽이다.
    # ══════════════════════════════════════════════════════

    def sync_balance(self):
        """느린 경로 — REST 폴링. 몇 초~수십 초 주기로 부른다.

        이게 '정답'이다. 웹소켓으로 추정해둔 값을 통째로 덮어쓴다.
        추정 로직에 버그가 있어도 폴링이 주기적으로 바로잡아주는 구조다.

        kis_api.balance() 가 잔고조회 응답을 (positions, cash, equity)로
        간추려준다 — positions 는 {종목코드: (수량, 평단)} 이고, 보유
        종목이 하나도 없으면 None 이다. 전부 KIS가 문자열로 주는 값이라
        여기서 float 로 바꾼다(안 바꾸면 이후 수량 계산에서 str+float 로
        터진다)."""
        pos, cash, equity = kis_api.balance()
        with self._lock:
            if cash is not None:
                self._cash = float(cash)
            if equity is not None:
                self._equity = float(equity)

            # ★ 통째로 새로 만든다(갱신이 아니라 교체) ★
            #   응답에 없는 종목은 이제 안 갖고 있다는 뜻이다 — 갱신만
            #   하면 전량 청산된 종목이 로컬에 영원히 낡은 값으로 남는다.
            new_positions: dict[str, Position] = {}
            for sym, (size, avg) in (pos or {}).items():
                # 현재가는 API 잔고응답이 아니라 웹소켓에서 온다.
                # 여기서 Position 을 통째로 새로 만들면 last_price 가 날아가므로
                # 기존 값을 꺼내 보존한다.
                last = self._positions.get(sym, Position(sym)).last_price
                new_positions[sym] = Position(sym, float(size), float(avg), last)
            self._positions = new_positions

            # 예약금도 살아있는 주문 기준으로 재계산한다.
            # 거부·취소 때 _reserved 를 못 빼서 생긴 누수를 여기서 청소하는 셈.
            self._reserved = sum(
                o.remaining * (o.price or 0)
                for o in self._orders.values()
                if o.is_alive and o.side is Side.BUY)

    def on_market(self, ev):
        """Engine.feed 가 모든 시장 이벤트에 대해 부른다.

        웹소켓이 밀어주는 값으로 시계와 현재가를 갱신한다.
        (backtrader 는 cerebro 에서 직접 읽으므로 이 메서드가 필요 없다 —
         push 냐 pull 이냐의 차이일 뿐 결과는 같다)

        이벤트 종류를 묻지 않는다 — ev.ref_price 가 알아서 답한다.
        틱이면 체결가, 봉이면 종가, 호가면 중간가."""
        self._now = ev.dt
        if ev.ref_price:
            self.on_price(ev.symbol, ev.ref_price)

    def on_price(self, symbol: str, price: float):
        """실시간 체결가(H0STCNT0) 수신 시 현재가 갱신.

        setdefault 를 쓰는 이유: 보유하지 않은 종목도 현재가는 필요하다.
        target_pct 가 '얼마어치 살까'를 계산할 때 pos.last_price 를 쓰는데,
        살 때는 아직 포지션이 없기 때문이다."""
        with self._lock:
            pos = self._positions.setdefault(symbol, Position(symbol))
            pos.last_price = price

    # ══════════════════════════════════════════════════════
    # 차이 ③ — 체결이 비동기다
    #
    # backtrader: buy() 하면 다음 봉에 자동 체결. 같은 스레드, 순서 보장.
    # KIS       : 주문 전송과 체결통보가 완전히 별개. 몇 초 뒤 다른 스레드로 온다.
    #             부분체결이면 여러 번 나눠서 온다.
    # ══════════════════════════════════════════════════════
    def on_execution_report(self, broker_id: str, status: str,
                            filled_qty: float, price: float, dt: datetime):
        """체결통보(H0STCNI0) 파싱 결과를 넣는다.

        반환: (Order, Fill|None)
          — 브로커는 '상태만' 바꾸고, 라우팅은 Engine.feed_execution 이 한다.
            브로커가 Trader 를 직접 부르면 순환 의존이 생긴다.
        """
        with self._lock:
            # ── 내 주문인지 확인 ────────────────────────────
            order = self._by_broker_id.get(str(broker_id))
            if order is None:
                # HTS로 직접 낸 주문, 또는 프로그램 재시작 이전에 낸 주문.
                # 무시하되 로그는 남긴다 — 포지션이 어긋나는 원인이 된다.
                log.warning("모르는 주문번호 %s — 수동주문이거나 재시작 이전 건", broker_id)
                return None, None

            fill = None
            if filled_qty > 0:
                # ── 1) Order 상태 갱신 ──────────────────────
                # apply_fill 이 평균체결가와 누적수량을 계산하고
                # 전량이면 FILLED, 아니면 PARTIAL 로 상태를 바꾼다.
                order.apply_fill(filled_qty, price, dt)

                # ── 2) Fill 생성 ───────────────────────────
                # TradeTracker 가 이걸 받아 손익을 계산한다.
                # 백테스트도 같은 Fill 을 만들기 때문에 손익식이 하나로 통일된다.
                fill = Fill(dt=dt, symbol=order.symbol, side=order.side,
                            size=filled_qty, price=price, order_id=order.id)

                # ── 3) 포지션 즉시 갱신 (빠른 경로) ──────────
                # 폴링을 기다리면 늦다. 직접 계산해 임시 반영한다.
                pos = self._positions.setdefault(order.symbol,
                                                 Position(order.symbol))
                signed = filled_qty if order.side is Side.BUY else -filled_qty
                total = pos.size + signed

                # 평단 갱신 세 갈래:
                #   ① 신규 진입(pos.size==0) → 체결가가 곧 평단
                #   ② 같은 방향 증가          → 가중평균 재계산
                #   ③ 감소(부분청산)          → 평단 유지
                #      (줄어들 때 평단이 바뀌면 남은 수량의 원가가 틀어진다)
                #
                # ★ pos.size >= 0 으로 쓰면 안 된다 ★
                #   신규 숏에서 (0>=0)=True vs (signed>0)=False 로 어긋나
                #   평단이 0으로 남는다.
                if total != 0:
                    if pos.size == 0:
                        pos.avg_price = price
                    elif (pos.size > 0) == (signed > 0):
                        pos.avg_price = (pos.avg_price * pos.size
                                         + price * signed) / total
                pos.size = total

                # ── 4) 예약금 해제 ─────────────────────────
                # 체결됐으니 묶어둔 돈을 푼다. max(0, ...) 는 추정 오차로
                # 음수가 되는 걸 막는 안전장치.
                if order.side is Side.BUY and order.price:
                    self._reserved = max(0.0, self._reserved
                                         - filled_qty * order.price)

                # 참고: 여기서 self._cash 는 건드리지 않는다.
                #       폴링이 곧 갱신해주므로 단일종목 전략에서는 문제없지만,
                #       같은 봉 안에서 '팔고 바로 다른 걸 사는' 전략이라면
                #       매도대금을 즉시 반영해야 한다.

            # ── 체결 외 상태 변화 ──────────────────────────
            if status == "cancel":
                order.status = OrderStatus.CANCELED
            elif status == "reject":
                order.status = OrderStatus.REJECTED

            return order, fill


# ══════════════════════════════════════════════════════════════════
# 삭제된 것들 — 어디로 갔는지
# ══════════════════════════════════════════════════════════════════
#
#   LiveRunner      → kis_bridge.EngineTrader
#                     기존 findata 의 trading_q 를 그대로 소비하도록 바뀌었다.
#   BarAggregator   → engine.BarFactory
#                     (종목, 주기) 조합마다 하나씩 만들어 전략들이 공유한다.
#                     전략마다 집계기를 따로 두는 건 낭비였다.
#   run_live()      → kis_bridge.EngineTrader + 기존 KiSEngine
#                     기동 순서(잔고동기화 → 워밍업 → 상태복원 → 실시간)는
#                     Engine.start / Trader.start 가 담당한다.