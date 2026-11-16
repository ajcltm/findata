"""
═══════════════════════════════════════════════════════════════════
 base.py — 전략 기반 (_IndSlot / Strategy). 사용자가 상속하는 클래스
═══════════════════════════════════════════════════════════════════

옛 위치: alpha/trader/trading.py 의 "4. Strategy" 절.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

from alpha.broker.base import Broker
from alpha.core.orders import Order
from alpha.core.trades import Trade
from alpha.events.events import Bar, MarketEvent

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 4. Strategy — 사용자가 상속하는 클래스
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class _IndSlot:
    """지표 하나 + 그 지표를 무엇으로 갱신할지.

    ■ 왜 지표를 감싸나
        '어떤 피드로 갱신하는가'는 지표의 속성이 아니다. 같은 SMA(20)을
        봉에도 틱에도 쓸 수 있어야 하고, 그 선택은 전략이 등록할 때 한다.
        지표 클래스에 kind/variant 를 넣으면 지표가 이벤트 체계를 알게 되어
        재사용이 막힌다.

        그래서 지표는 update/value/ready/warmup 만 알고, 매칭은 이 슬롯이 한다.
        matches() 가 지표가 아니라 여기 있는 이유다."""
    ind: object             # 지표 본체 (SMA, ATR, ...)
    kind: str = "bar"       # "bar" | "tick" | "quote" | 앞으로 생길 kind
    variant: object = None  # 봉이면 주기(초). None 이면 그 kind 아무거나
    symbol: Optional[str] = None    # None 이면 구독 중인 전 종목
    override: Optional[str] = None  # 이름을 직접 지정하고 싶을 때

    def matches(self, ev: MarketEvent) -> bool:
        """★ symbol 을 빼먹으면 안 된다 ★
        두 종목을 구독한 전략에서 symbol 을 안 보면 7만원짜리와 20만원짜리
        가격이 한 SMA 에 섞여 들어간다. 값이 조용히 무의미해진다."""
        return (self.kind == ev.kind
                and (self.variant is None or self.variant == ev.variant)
                and (self.symbol is None or self.symbol == ev.symbol))

    @property
    def key(self) -> tuple:
        return (self.symbol, self.kind, self.variant)

    @property
    def label(self) -> str:
        """기록·로그용 식별자. 지표 이름 + 맥락(종목·주기).

            SMA20                    단일 종목, 봉 주기 하나
            SMA20@005930             여러 종목을 구독할 때
            SMA20@005930@60s         멀티 타임프레임일 때
            SMA20_price@005930@tick  틱 지표

        지표 본체는 자기 이름만 알고, 종목·피드는 여기서 붙인다.
        override 가 있으면 그것을 쓴다 — 'fast'/'slow' 처럼 의미로
        부르고 싶을 때가 있다."""
        if self.override:
            return self.override
        parts = [self.ind.name]
        if self.symbol:
            parts.append(self.symbol)
        if self.kind != "bar":
            parts.append(self.kind)
        elif self.variant:
            parts.append(f"{self.variant}s")
        return "@".join(parts)


class Strategy:
    """전략 베이스.

    ■ 이 클래스는 실행 모드를 모른다
        self.broker 가 시뮬레이션인지 실계좌인지 알 방법이 없고,
        알 필요도 없다. 그게 이 설계의 목적이다.

    ■ broker 는 언제 생기나
        Trader.__init__ 이 밖에서 꽂아준다. 그래서 setup() 안에서는
        아직 없다. 브로커가 필요한 초기화는 on_start() 에 쓸 것.
    """
    defaults: dict = {}         # 파라미터 기본값. 하위 클래스가 덮어쓴다

    # Trader 가 주입한다 (타입 힌트일 뿐, 여기서 대입하지 않음)
    broker: Broker
    trader: "Trader"

    def __init__(self, **params):
        # defaults 위에 사용자 인자를 덮어써서 self.p 를 만든다.
        # SimpleNamespace 라서 self.p.fast 처럼 점으로 접근된다.
        self.p = SimpleNamespace(**{**self.defaults, **params})
        self._indicators = []
        # 프레임워크 배선이 끝난 뒤 사용자 코드를 부른다.
        # 이 순서 덕분에 사용자는 super().__init__() 을 기억할 필요가 없다.
        self.setup()

    def ind(self, indicator, on: str = "bar", variant=None, symbol=None,
            name: Optional[str] = None):
        """지표 등록. 선언한 피드가 올 때마다 자동으로 update 된다.

        ■ on / variant — 어떤 이벤트로 갱신할지
            ind(SMA(20))                        1분봉이든 5분봉이든 오는 봉으로
            ind(SMA(20), on="bar", variant=60)  1분봉으로만
            ind(SMA(20), on="bar", variant=300) 5분봉으로만
            ind(SMA(20), on="tick")             체결 틱마다
            ind(Imbalance(), on="quote")        호가 갱신마다

        ■ symbol — 여러 종목을 구독할 때 필수
            ind(SMA(20), on="tick")                     구독 중인 전 종목이 섞인다
            ind(SMA(20), on="tick", symbol="005930")    이 종목 틱만

            ★ 두 종목 이상 구독하면 반드시 지정할 것 ★
              안 하면 7만원짜리와 20만원짜리 가격이 한 지표에 섞여
              값이 조용히 무의미해진다. 종목마다 세트가 필요하면
              per_symbol() 을 쓰는 게 편하다.

          지표를 봉으로만 돌린다는 가정을 두지 않는다. 체결강도 이동평균,
          호가 불균형 평활 같은 건 봉이 아니라 틱·호가로 굴러야 한다.

          ★ 멀티 타임프레임에서 variant 를 반드시 지정할 것 ★
            1분봉과 5분봉을 둘 다 구독하는데 variant 를 비워두면
            같은 지표가 두 주기로 번갈아 갱신되어 값이 뒤섞인다.

        ■ 지표가 읽는 필드
            SMA(period, src="close") 처럼 src 를 필드명으로 받는다.
            봉이면 "close", 틱이면 "price" 를 넘기면 그대로 동작한다.
                ind(SMA(20, src="price"), on="tick")

        ★ 등록 순서 = 계산 순서 ★
          CrossOver 처럼 다른 지표를 입력으로 받는 것은 반드시 뒤에 등록.
          먼저 등록하면 갱신 안 된 값으로 계산해서 '조용히' 틀린다.

        ■ 넣는 것과 돌려주는 것이 다르다
            내부 목록에는 _IndSlot(지표 + kind + variant)이 들어가고,
            반환은 지표 본체다. 전략에서 self.fast.value 로 쓰려면
            본체여야 하고, 매칭 정보는 전략이 알 필요가 없기 때문이다."""
        slot = _IndSlot(indicator, on, variant, symbol, name)
        # 같은 label 이 둘이면 기록에서 구별이 안 된다. 등록 시점에 잡는다.
        if any(s.label == slot.label for s in self._indicators):
            log.warning("지표 이름 충돌: %s — name= 로 구분해 주세요", slot.label)
        self._indicators.append(slot)
        return indicator

    def per_symbol(self, factory, symbols, on: str = "bar", variant=None):
        """종목마다 지표를 하나씩 만들어 등록한다. {종목: 지표} 를 돌려준다.

            self.sma = self.per_symbol(lambda: SMA(20), SYMBOLS, on="tick")
            ...
            self.sma[t.symbol].value

        factory 가 함수인 이유: 종목마다 '별개의' 인스턴스가 필요하다.
        하나를 만들어 돌려쓰면 모든 종목이 같은 버퍼를 공유해 섞인다."""
        return {sym: self.ind(factory(), on=on, variant=variant, symbol=sym)
                for sym in symbols}

    def indicator_snapshot(self, ev: Optional[MarketEvent] = None) -> list[tuple[str, str, Optional[float]]]:
        """지금까지 낸 지표 값 전부. [(label, line, value), ...]

        기록·로그용. Trader 가 이벤트 처리 직후(훅 호출 직전)에 찍으면
        '전략이 판단할 때 본 값'이 남는다.

        ev 를 주면 그 이벤트에 매칭되는(= 방금 갱신된) 지표만 돌려준다.
        안 주면 등록된 지표 전부의 현재 상태를 돌려준다.

        ■ 왜 필요한가 (ev 필터)
            한 전략이 여러 봉 주기(예: 60초+300초)를 같이 구독하면,
            필터 없이 전부 돌려줄 경우 60초봉이 마감될 때마다 아직
            안 바뀐 300초봉 지표까지 매번 같이 기록돼 중복이 쌓인다.
            _record_indicators 가 이걸로 '방금 이 이벤트가 실제로
            갱신한 지표'만 골라 기록한다.

        ■ (label, line, value) 인 이유
            지표 하나가 라인을 여러 개 낼 수 있다(MACD의 macd/signal/
            histo 등, indicators.py 참고). 라인이 하나뿐인 지표도 그
            라인 이름 그대로 나온다(관례상 "value")."""
        slots = (self._indicators if ev is None
                else [s for s in self._indicators if s.matches(ev)])
        return [(slot.label, line, val)
                for slot in slots
                for line, val in slot.ind.values().items()]

    @property
    def ready(self) -> bool:
        """등록한 지표가 전부 값을 낼 수 있나. SMA(60)은 60봉이 쌓여야 True.

        보수적으로 '전부'를 본다. 봉 지표와 틱 지표를 섞어 쓰면 둘 다
        데워질 때까지 훅이 안 불린다 — 어중간한 상태로 주문하는 것보다 낫다."""
        return all(slot.ind.ready for slot in self._indicators)

    def required_history(self) -> dict[tuple, int]:
        """지표들이 요구하는 워밍업 분량. {(symbol, kind, variant): 개수}

            {("005930","bar",60): 60, (None,"tick",None): 50}
            symbol 이 None 이면 '구독 중인 아무 종목이나'라는 뜻이다.

        같은 피드를 여러 지표가 쓰면 가장 큰 값을 취한다.
        warmup 을 모르는 지표(None)는 세지 않으므로, 그런 지표가 있으면
        실시간으로 데워질 때까지 기다리게 된다.

        Engine.add 에 넘길 history 를 준비할 때 이걸 보고 REST 조회량을
        정하면 된다."""
        need: dict[tuple[str, object], int] = {}
        for slot in self._indicators:
            n = getattr(slot.ind, "warmup", None)
            if not n:
                continue
            need[slot.key] = max(need.get(slot.key, 0), int(n))
        return need

    def _update_indicators(self, ev: MarketEvent):
        """Trader 가 모든 이벤트에 대해 부른다. 필터는 여기서 한다.

        등록 순서대로 돌되, 선언한 피드와 맞는 것만 갱신한다."""
        for slot in self._indicators:
            if slot.matches(ev):
                slot.ind.update(ev)

    # ───────── 사용자가 구현하는 훅 ─────────
    def setup(self):
        """지표 생성, 초기 변수. broker 는 아직 없다."""

    def on_start(self):
        """워밍업·상태복원 완료 후 1회. 여기서는 broker 를 쓸 수 있다."""

    def on_bar(self, bar: Bar):
        """봉이 확정될 때마다.

        베이스의 훅들은 전부 '아무것도 안 함' 기본 구현이다.
        raise 하지 않는 이유: 구독했는데 훅을 안 만들었으면
        EventRouter.register 가 등록 시점에 경고한다. 실행 중에
        매 봉 예외를 던지는 것보다 그쪽이 훨씬 빨리 눈에 띈다."""

    def on_order(self, order: Order):
        """주문 상태 변화(접수/체결/거부/취소)."""

    def on_trade(self, trade: Trade):
        """거래가 완결되어 손익이 확정됐을 때."""

    def on_timer(self, now: datetime):
        """주기적 호출. 봉과 무관한 시각 기반 로직(종가청산 등)."""

    def on_stop(self):
        """종료 시 정리."""

    # ───────── 재시작 대비 ─────────
    def to_state(self) -> dict:
        """봉으로 복원할 수 없는 것만 저장한다.

        지표는 과거 봉을 다시 흘리면 복원되지만,
        '진입 후 최고가 기준 트레일링 스탑' 같은 건 봉에 없는 정보라
        저장하지 않으면 영영 잃는다."""
        return {}

    def from_state(self, state: dict): ...
