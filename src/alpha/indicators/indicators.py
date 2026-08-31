"""
═══════════════════════════════════════════════════════════════════
 indicators.py — 증분(incremental) 지표
═══════════════════════════════════════════════════════════════════

■ 왜 직접 만드나
    pandas 로 df.close.rolling(20).mean() 하면 백테스트는 되지만
    실전에서는 못 쓴다. 실전은 봉이 하나씩 오기 때문이다.
    그러면 백테스트용/실전용 지표를 따로 만들게 되고,
    두 계산이 미묘하게 달라지는 순간 백테스트 결과를 믿을 수 없게 된다.

    증분으로 통일하면 그 문제가 원천적으로 안 생긴다.
    대가는 속도다 — 벡터 연산을 못 쓰니 긴 백테스트가 느려진다.

■ 공통 규약
    update(ev) -> float | None       대표 라인 값. 없으면 None (워밍업 부족)
    .values()                        지금까지 낸 라인 전부. {"macd": 1.2, ...}
    .line(이름)                      라인 하나만 이름으로 꺼낸다
    .value                           라인이 정확히 하나일 때만 그 값(편의)
    .ready                           지금 낸 라인들 중 None 이 없는가

■ 라인 — 지표 하나가 값을 여러 개 낼 수 있다
    MACD(macd/signal/histo)처럼 값이 여럿인 지표는 update() 안에서
    self._values["라인이름"] = 값 을 원하는 만큼 채우면 된다. 미리
    선언할 필요가 없다 — "이 지표는 라인이 몇 개다"를 베이스 클래스에
    알릴 필요가 없다는 뜻이다.

    값을 하나만 내는(대부분의) 지표는 관례상 라인 이름을 "value" 하나만
    쓴다 — 그러면 .value 로 바로 읽히고(라인이 정확히 하나면 자동으로
    그걸 가리킨다), 일반 지표 화면(콘솔 뷰)에서 다중 라인 지표와
    where={"line": "value"} 로 자동 구분된다.

    ★ "주 라인"을 강제하지 않는다 ★
      MACD 같은 지표를 만들 때 "이 중 어떤 라인이 대표냐"를 정할 필요가
      없다 — 그냥 .line("macd")/.line("signal")/.line("histo") 로 셋 다
      똑같이 읽는다. .value 는 라인이 하나뿐인 지표에서만 의미가 있고,
      여러 개면 None 이다(어느 걸 골라야 할지 이 클래스가 판단할 근거가
      없으므로 — 호출자가 .line(이름)으로 명시해야 한다).

■ 입력은 봉만이 아니다
    update() 가 받는 건 Bar 일 수도 Tick 일 수도 Quote 일 수도, 다른
    지표의 원시 출력값(_Scalar 로 감싼 것)일 수도 있다.
    src 를 필드명으로 받는 지표(SMA/EMA)는 그대로 재사용된다:
        SMA(20, src="close")   봉의 종가
        SMA(20, src="price")   틱의 체결가
    등록할 때 어떤 피드로 갱신할지 선언한다:
        self.ind(SMA(20), on="bar", variant=60)
        self.ind(SMA(20, src="price"), on="tick")

■ 주의
    CrossOver 처럼 다른 지표를 입력으로 받는 것은
    Strategy.ind() 등록 순서상 반드시 입력 지표들보다 '뒤'에 와야 한다.
"""

from __future__ import annotations

import functools
import inspect
from collections import deque
from typing import Optional

from alpha.events.events import Bar, MarketEvent, Quote, Tick


class Indicator:
    """모든 지표의 베이스.

    ■ 이름은 생성 인자에서 자동으로 만들어진다
        하위 클래스가 무엇을 인자로 받든 그대로 이름이 된다.
        'close' 나 'period' 같은 특정 이름을 베이스가 알 필요가 없다.

            SMA(20)                  → "SMA(20)"
            SMA(3, src="price")      → "SMA(3,src=price)"
            SMA(20, src="close")     → "SMA(20)"        기본값은 생략
            CrossOver(f, s)          → "CrossOver(SMA(5),SMA(20))"

        기본값과 같은 인자는 빼서 이름을 짧게 유지한다. 값이 다르면
        붙는다 — 그래야 SMA(5)와 SMA(20)이 구별된다.

    ■ 직접 짓고 싶으면 name= 을 넘긴다
        모든 지표가 자동으로 받는다. 하위 클래스가 __init__ 에
        선언할 필요 없다.

            SMA(20, name="장기추세")  → "장기추세"
    """
    _name: Optional[str] = None
    _args: dict = {}
    _defaults: dict = {}

    def __init_subclass__(cls, **kw):
        """하위 클래스의 __init__ 을 감싸서 생성 인자를 기록한다.

        이 방식을 쓰는 이유: 하위 클래스가 super().__init__() 을 부르도록
        강제하면 언젠가 빼먹는다. __init_subclass__ 는 클래스 정의 시점에
        한 번만 돌고 하위 클래스 코드를 건드리지 않는다.

        self._values 를 여기서 만드는 이유도 같다 — 라인 저장소는
        인스턴스마다 따로 있어야 하는데(클래스 속성으로 두면 모든
        인스턴스가 공유해 값이 섞인다), 하위 클래스 __init__ 이 전부
        super().__init__() 을 부르진 않으므로 이 래퍼가 대신 만들어준다."""
        super().__init_subclass__(**kw)

        orig = cls.__init__
        if getattr(orig, "_wrapped", False):
            return                              # 이미 감싼 것(다중 상속) 방지

        sig = inspect.signature(orig)
        params = list(sig.parameters.values())[1:]      # self 제외
        defaults = {p.name: p.default for p in params
                    if p.default is not inspect.Parameter.empty}

        @functools.wraps(orig)
        def __init__(self, *args, name=None, **kwargs):
            self._values: dict[str, Optional[float]] = {}   # 라인이름 -> 값
            orig(self, *args, **kwargs)
            self._name = name
            bound = sig.bind(self, *args, **kwargs)
            bound.apply_defaults()
            self._args = {k: v for k, v in list(bound.arguments.items())[1:]}
            self._defaults = defaults

        __init__._wrapped = True
        cls.__init__ = __init__

    @property
    def name(self) -> str:
        """지표 자기 설명. 종목·주기 같은 맥락은 _IndSlot 이 붙인다.

        ■ 왜 프로퍼티인가
            per_symbol(lambda: SMA(20), SYMS) 로 만들면 종목마다 인스턴스가
            생기는데, 이름을 생성 인자로만 받으면 전부 같아진다.
            지표는 '자기가 무엇인지'만 말하고 구별은 슬롯이 한다."""
        if self._name:
            return self._name

        def token(k, v):
            # 인자가 지표면 그 이름을 쓴다 — CrossOver(SMA(5),SMA(20))
            t = v.name if isinstance(v, Indicator) else str(v)
            # 기본값이 있는 인자는 k=v 로, 필수 인자는 값만 (읽기 편하게)
            return f"{k}={t}" if k in self._defaults else t

        # 기본값과 같은 인자는 생략해 이름을 짧게 유지한다.
        parts = [token(k, v) for k, v in self._args.items()
                 if not (k in self._defaults and v == self._defaults[k])]

        # 다만 전부 생략되면 RSI(14)가 "RSI()"가 되어 정보가 사라진다.
        # 그럴 때는 인자를 다 보여준다 — 기록 라벨은 짧기보다 명확해야 한다.
        if not parts and self._args:
            parts = [token(k, v) for k, v in self._args.items()]

        return f"{type(self).__name__}({','.join(parts)})"

    @property
    def warmup(self) -> Optional[int]:
        """값을 내기까지 필요한 이벤트 개수. 모르면 None.

        기본값은 period 인자다. 다르게 필요하면 하위 클래스가 오버라이드한다
        (RSI 는 첫 봉에서 변화량을 못 구해 period+1, CrossOver/MACD 는
        입력 기준)."""
        return self._args.get("period")

    # ───────── 라인 접근 ─────────
    def line(self, name: str) -> Optional[float]:
        """라인 하나를 이름으로 꺼낸다. 단일 라인 지표에도 그대로 동작한다
        (sma.line("value") == sma.value)."""
        return self._values.get(name)

    def values(self) -> dict[str, Optional[float]]:
        """지금까지 낸 라인 전부의 사본. {"macd": 1.2, "signal": 1.0, ...}
        indicator_snapshot()/기록/뷰가 이걸로 라인을 전부 훑는다."""
        return dict(self._values)

    @property
    def value(self) -> Optional[float]:
        """라인이 정확히 하나일 때만 그 값을 돌려준다(단일 라인 지표용
        편의). 라인이 여럿이면 어느 걸 대표로 삼을지 이 클래스가 정할
        근거가 없으므로 None — .line("이름")으로 명시해서 읽을 것."""
        if len(self._values) == 1:
            return next(iter(self._values.values()))
        return None

    @property
    def ready(self) -> bool:
        """값을 하나라도 냈고, 지금 낸 라인들 중 None 이 없으면 준비된
        것으로 본다. 사정이 다른 지표(예: CrossOver — 0도 정상값이라
        None 여부로 못 가른다)는 오버라이드한다."""
        return bool(self._values) and all(v is not None for v in self._values.values())

    def update(self, ev) -> Optional[float]:
        raise NotImplementedError


class _Scalar:
    """다른 지표의 출력(원시 숫자)을, 'bar.close'처럼 필드로 값을 읽는
    지표(SMA/EMA 등)에 그대로 먹이기 위한 최소 래퍼.

    예) MACD의 signal 선은 '봉'이 아니라 'macd 값'의 EMA다:
        self.signal_ema = EMA(9, src="v")
        self.signal_ema.update(_Scalar(macd_value))
    """
    __slots__ = ("v",)

    def __init__(self, v: float):
        self.v = v


class SMA(Indicator):
    """단순이동평균. 최근 N봉의 산술평균.

    ■ 매번 sum() 하지 않는 이유
        N이 200이면 봉마다 200번 더한다. 대신 합계를 들고 다니면서
        나가는 값을 빼고 들어오는 값을 더하면 봉당 연산 2번으로 끝난다.
    """

    def __init__(self, period: int, src: str = "close"):
        self.period = period
        self.src = src                          # "close" / "high" 등
        self.buf = deque(maxlen=period)         # maxlen 초과분은 앞에서 자동 제거
        self._sum = 0.0                         # buf 안 값들의 합계

    def update(self, bar):
        x = getattr(bar, self.src)              # bar.close 등을 문자열로 접근

        # 버퍼가 꽉 찼으면, 곧 밀려날 맨 앞 값을 합계에서 미리 뺀다.
        # (append 하면 deque 가 알아서 버리므로 그 전에 빼야 한다)
        if len(self.buf) == self.period:
            self._sum -= self.buf[0]

        self.buf.append(x)
        self._sum += x

        # N개가 다 차기 전에는 평균이 의미 없으므로 None.
        value = self._sum / self.period if len(self.buf) == self.period else None
        self._values["value"] = value
        return value


class EMA(Indicator):
    """지수이동평균. 최근 값에 더 큰 가중치.

    ■ 계산
        새 EMA = 이번값 × k + 이전EMA × (1-k),   k = 2/(N+1)
        k 가 클수록(N이 작을수록) 최근 값에 민감해진다.

    ■ 첫 값은 SMA 로 시드한다
        이전EMA 가 있어야 계산되는데 맨 처음엔 없다.
        첫 N개의 단순평균을 시작점으로 삼는 게 관행이다(backtrader 동일).
    """

    def __init__(self, period: int, src: str = "close"):
        self.period = period
        self.src = src
        self.k = 2.0 / (period + 1)             # 평활계수
        self.buf = deque(maxlen=period)         # 시드용. 시드 후엔 안 쓴다

    def update(self, bar):
        x = getattr(bar, self.src)
        cur = self._values.get("value")

        if cur is None:
            # ── 시드 단계 ── N개 모아서 단순평균
            self.buf.append(x)
            if len(self.buf) == self.period:
                cur = sum(self.buf) / self.period
        else:
            # ── 정상 단계 ── 이전값과 섞는다
            cur = x * self.k + cur * (1 - self.k)

        self._values["value"] = cur
        return cur


class RSI(Indicator):
    """상대강도지수. 0~100. 최근 상승폭과 하락폭의 비율.

    ■ 읽는 법
        70 이상 = 최근 오르기만 했다(과매수), 30 이하 = 과매도

    ■ 계산 (Wilder 평활)
        ① 전봉 대비 변화를 상승분(gain)과 하락분(loss)으로 분리
           +5원 → gain 5, loss 0    /   -3원 → gain 0, loss 3
        ② 각각의 평균을 구한다 (첫 N개는 단순평균, 이후는 아래 식)
           새평균 = (이전평균 × (N-1) + 이번값) ÷ N
           ※ EMA 와 비슷하지만 k = 1/N 을 쓰는 Wilder 방식이다
        ③ RS = 평균상승 ÷ 평균하락
           RSI = 100 - 100/(1+RS)
           → 하락이 0이면 RS 가 무한대 → RSI 100
    """

    def __init__(self, period: int = 14):
        self.period = period
        self.prev_close = None                  # 변화량 계산용 직전 종가
        self.avg_gain = self.avg_loss = None
        self.gains = deque(maxlen=period)       # 시드용
        self.losses = deque(maxlen=period)

    def update(self, bar):
        x = bar.close

        # 첫 봉은 변화량을 계산할 수 없다
        if self.prev_close is None:
            self.prev_close = x
            return None

        d = x - self.prev_close
        self.prev_close = x
        g = max(d, 0.0)             # 올랐으면 그 폭, 내렸으면 0
        l = max(-d, 0.0)            # 내렸으면 그 폭(양수), 올랐으면 0

        if self.avg_gain is None:
            # ── 시드 단계 ── N개 모아 단순평균
            self.gains.append(g)
            self.losses.append(l)
            if len(self.gains) == self.period:
                self.avg_gain = sum(self.gains) / self.period
                self.avg_loss = sum(self.losses) / self.period
        else:
            # ── Wilder 평활 ── 이전평균에 이번값을 1/N 비중으로 섞는다
            n = self.period
            self.avg_gain = (self.avg_gain * (n - 1) + g) / n
            self.avg_loss = (self.avg_loss * (n - 1) + l) / n

        if self.avg_gain is None:
            return None

        if self.avg_loss == 0:
            value = 100.0                        # 하락이 전혀 없었다
        else:
            rs = self.avg_gain / self.avg_loss
            value = 100.0 - 100.0 / (1.0 + rs)
        self._values["value"] = value
        return value


class ATR(Indicator):
    """평균실제변동폭. '이 종목이 한 봉에 보통 얼마나 흔들리나'를 원 단위로.

    ■ 왜 쓰나
        손절선을 -3% 로 고정하면 삼성전자는 정상 등락에도 털리고
        변동성 큰 종목은 3%가 순식간에 지나가 손절 역할을 못 한다.
        ATR 로 잡으면 종목마다 자동으로 맞춰진다.

    ■ True Range — 고가-저가만 보면 갭을 놓친다
        어제 10,000에 마감했는데 오늘 12,000에 시작해 12,100까지 갔다면
        고가-저가는 100뿐이지만 실제 움직임은 2,100이다.
        그래서 셋 중 최댓값을 쓴다:
            ① 오늘 고가 - 오늘 저가
            ② |오늘 고가 - 어제 종가|      (상승 갭 포착)
            ③ |오늘 저가 - 어제 종가|      (하락 갭 포착)

    ■ 그 TR 의 평균을 Wilder 방식으로 낸다
    """

    def __init__(self, period: int = 14):
        self.period = period
        self.prev_close = None
        self.buf = deque(maxlen=period)         # 시드용

    def update(self, bar):
        if self.prev_close is None:
            tr = bar.high - bar.low             # 첫 봉은 갭을 알 수 없다
        else:
            tr = max(bar.high - bar.low,
                     abs(bar.high - self.prev_close),
                     abs(bar.low - self.prev_close))
        self.prev_close = bar.close

        cur = self._values.get("value")
        if cur is None:
            self.buf.append(tr)
            if len(self.buf) == self.period:
                cur = sum(self.buf) / self.period
        else:
            n = self.period
            cur = (cur * (n - 1) + tr) / n

        self._values["value"] = cur
        return cur


class CrossOver(Indicator):
    """두 지표의 교차 감지.  1=상향돌파, -1=하향돌파, 0=변화없음.

    ■ 원리
        차이(a - b)의 '부호가 바뀌는 순간'을 잡는다.
            이전 차이 ≤ 0 인데 지금 > 0  → a 가 b 를 뚫고 올라감 → +1
            이전 차이 ≥ 0 인데 지금 < 0  → 뚫고 내려감           → -1

    ■ a.value / b.value 를 쓰는 이유
        a, b 는 아무 지표나 받는다(보통 SMA 둘). 라인이 하나뿐인
        지표라면 .value 가 자동으로 그 값을 가리키므로 이름을 몰라도
        된다 — a, b 가 라인을 여러 개 내는 지표(MACD 등)면 .value 가
        None 이 되므로 쓸 수 없다(그럴 땐 a.line("macd") 처럼 직접
        지표를 감싸서 넘길 것).

    ■ ★ 등록 순서 주의 ★
        입력 지표(a, b)가 먼저 update 된 뒤에 이 지표가 update 돼야 한다.
        Strategy.ind() 등록 순서가 곧 계산 순서다.
            self.fast  = self.ind(SMA(20))
            self.slow  = self.ind(SMA(60))
            self.cross = self.ind(CrossOver(self.fast, self.slow))   # 반드시 뒤
        순서를 어기면 갱신 안 된 값으로 계산해서 '조용히' 틀린다.
    """

    def __init__(self, a: Indicator, b: Indicator):
        self.a, self.b = a, b
        self.prev_diff = None                   # 직전 봉의 (a - b)
        self._values["value"] = 0.0             # 교차 없음이 기본값

    @property
    def warmup(self) -> Optional[int]:
        """입력 지표가 데워진 뒤 '직전 차이'를 알려면 1개가 더 필요하다."""
        a, b = self.a.warmup, self.b.warmup
        if a is None or b is None:
            return None
        return max(a, b) + 1

    @property
    def ready(self):
        """다른 지표와 달리 값이 0이어도 정상이므로 베이스의 기본 ready
        (None 여부)로 판정할 수 없다. '직전 차이를 알고 있는가'로 본다."""
        return self.prev_diff is not None

    def update(self, bar=None):
        # 입력 지표가 아직 워밍업 중이면 판단 보류
        if not (self.a.ready and self.b.ready):
            self._values["value"] = 0.0
            return 0.0

        cur = self.a.value - self.b.value

        # 첫 계산이면 비교 대상이 없다. 차이만 기록하고 넘어간다.
        if self.prev_diff is None:
            self.prev_diff = cur
            self._values["value"] = 0.0
            return 0.0

        if self.prev_diff <= 0 < cur:
            value = 1.0                          # 음(또는 0) → 양 : 상향돌파
        elif self.prev_diff >= 0 > cur:
            value = -1.0                         # 양(또는 0) → 음 : 하향돌파
        else:
            value = 0.0                          # 부호 그대로

        self.prev_diff = cur
        self._values["value"] = value
        return value


class MACD(Indicator):
    """이동평균수렴확산(Moving Average Convergence Divergence).

    ■ 세 라인 — 셋 다 대등하다(대표 라인 없음)
        macd    단기EMA - 장기EMA. 0선 위면 상승추세, 아래면 하락추세.
        signal  macd 의 EMA. macd 가 이 선을 위/아래로 뚫는 순간이 흔히
                쓰는 매매 신호다.
        histo   macd - signal. 0 근처에서 부호가 바뀌는 순간이 signal
                교차와 같은 타이밍이라, 막대그래프로 보통 표현한다.

    ■ EMA 를 재사용하는 이유
        macd/signal 둘 다 EMA 계산이고, 이미 있는 EMA 와 완전히 같은
        공식이어야 한다. 따로 구현하면 두 계산이 미묘하게 달라질
        여지가 생긴다.

    ■ signal 은 '봉'이 아니라 'macd 값'의 EMA다
        EMA.update() 는 getattr(bar, src) 로 값을 읽으므로, macd 값을
        그대로 넣을 수 없다. _Scalar 로 감싸서 넣는다.

    등록: self.macd = self.ind(MACD(12, 26, 9), override="MACD")
    조회: self.macd.line("macd") / .line("signal") / .line("histo")
         (.value 는 라인이 셋이라 항상 None — 반드시 .line(이름)으로 읽을 것)
    """

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9,
                 src: str = "close"):
        self.fast_ema = EMA(fast, src=src)
        self.slow_ema = EMA(slow, src=src)
        self.signal_ema = EMA(signal, src="v")   # _Scalar.v 를 읽는다

    @property
    def warmup(self) -> Optional[int]:
        """macd 가 나오기 시작한 뒤로 signal 개를 더 모아야 signal/histo
        가 나온다."""
        f, s, sig = self.fast_ema.warmup, self.slow_ema.warmup, self.signal_ema.warmup
        if f is None or s is None or sig is None:
            return None
        return max(f, s) + sig

    def update(self, bar):
        f = self.fast_ema.update(bar)
        s = self.slow_ema.update(bar)
        if f is None or s is None:
            return None                          # 아직 fast/slow 도 안 데워짐

        macd_val = f - s
        self._values["macd"] = macd_val

        sig_val = self.signal_ema.update(_Scalar(macd_val))
        self._values["signal"] = sig_val
        self._values["histo"] = None if sig_val is None else macd_val - sig_val
        return macd_val


# ═══════════════════════════════════════════════════════════
# 틱·호가 전용 지표 — 봉으로는 만들 수 없는 것들
# ═══════════════════════════════════════════════════════════

class TickImbalance(Indicator):
    """체결 방향 불균형. -1(매도 우세) ~ +1(매수 우세).

    ■ 봉으로는 못 구한다
        봉에는 '매수체결이었나 매도체결이었나'가 없다.
        Tick.side 는 KIS 체결구분(1=매도, 5=매수)에서 온다.

    ■ 계산
        최근 N틱 중  (매수건수 - 매도건수) / N
        20틱 중 15건이 매수 → (15-5)/20 = +0.5

    등록: self.ind(TickImbalance(50), on="tick")
    """

    def __init__(self, period: int = 50):
        self.period = period
        self.buf = deque(maxlen=period)     # +1 매수, -1 매도

    def update(self, ev):
        side = getattr(ev, "side", "")
        if side == "buy":
            self.buf.append(1)
        elif side == "sell":
            self.buf.append(-1)
        else:
            return self.line("value")              # 방향 불명 — 이전 값 유지

        if len(self.buf) < self.period:
            return None                     # 아직 덜 찼다
        value = sum(self.buf) / self.period
        self._values["value"] = value
        return value


class SpreadEMA(Indicator):
    """호가 스프레드의 지수이동평균.

    ■ 왜 필요한가
        스프레드가 평소보다 벌어져 있으면 시장가 주문의 슬리피지가 커진다.
        '지금 들어가도 되는 장인가'를 판단하는 데 쓴다.

    등록: self.ind(SpreadEMA(20), on="quote")
    """

    def __init__(self, period: int = 20):
        self.period = period
        self.k = 2.0 / (period + 1)
        self.buf = deque(maxlen=period)

    def update(self, ev):
        sp = getattr(ev, "spread", None)
        if sp is None:
            return self.line("value")              # 호가가 한쪽만 있으면 건너뛴다

        cur = self._values.get("value")
        if cur is None:
            self.buf.append(sp)
            if len(self.buf) == self.period:
                cur = sum(self.buf) / self.period
        else:
            cur = sp * self.k + cur * (1 - self.k)

        self._values["value"] = cur
        return cur


class DOBI(Indicator):
    """1호가 잔량 기반 Dynamic Order Book Imbalance.

    ■ Quote.imbalance(events.py)와 다른 지표다
        그쪽은 전체 호가 잔량 합계로 계산한다. 이건 1호가(최우선 호가)의
        잔량만 쓴다 — 체결 직전 큐에 가장 가까운 자리라, 미세한 매수/매도
        압력 변화가 먼저 드러난다.

    ■ 세 라인 — 셋 다 대등하다(대표 라인 없음, MACD와 같은 이유)
        imbalance  (매수1호가잔량 - 매도1호가잔량) / (둘의 합). -1~+1.
        dobi       imbalance 의 변화량(이번 - 직전). 잔량이 밀리는 속도.
        filtered   dobi 에 칼만 필터를 적용해 잡음을 줄인 값. 실전 신호로는
                   보통 이 값을 쓴다.

    ■ 칼만 필터 파라미터
        var_err  관측(dobi) 잡음의 분산 — 클수록 매 순간의 관측을 덜 믿는다
        var_sig  상태(filtered)가 실제로 움직이는 폭의 분산 — 클수록 상태가
                 빨리 바뀐다고 본다
        초기 오차공분산(_p)을 var_err 보다 훨씬 크게 잡아두면, 데이터가
        쌓이기 전(칼만 게인이 1에 가까움)에는 관측값을 거의 그대로
        따라가다가, 이후 서서히 평활된다.

    등록: self.ind(DOBI(), on="quote", symbol=sym)  (여러 종목을 구독하는
          전략에서는 symbol= 필수 — 안 주면 종목 잔량이 섞인다. 종목마다
          별도 인스턴스가 필요하면 per_symbol() 을 쓸 것)
    조회: self.dobi.line("imbalance") / .line("dobi") / .line("filtered")
         (.value 는 라인이 셋이라 항상 None — 반드시 .line(이름)으로 읽을 것)
    """

    def __init__(self, var_err: float = 7_000_000, var_sig: float = 100):
        self.var_err = var_err
        self.var_sig = var_sig
        self._a = 0.0                    # 칼만 필터 상태(추정치) = filtered
        self._p = var_err * (10 ** 7)    # 칼만 필터 오차공분산(초기값을 크게)
        self._prev_imbalance = None

    def update(self, quote):
        bid_qty = quote.bid_sizes[0] if quote.bid_sizes else 0.0
        ask_qty = quote.ask_sizes[0] if quote.ask_sizes else 0.0
        total = bid_qty + ask_qty

        if total == 0:
            return None          # 양쪽 다 잔량이 없다 — 이번 갱신은 건너뛴다

        imbalance = (bid_qty - ask_qty) / total

        # 최초 값은 비교할 직전 값이 없어 변화량을 0으로 둔다
        # (CrossOver 의 '첫 계산은 차이만 기록' 과 같은 규칙).
        if self._prev_imbalance is None:
            dobi = 0.0
        else:
            dobi = imbalance - self._prev_imbalance
        self._prev_imbalance = imbalance

        # 칼만 필터 — dobi 를 관측치로, self._a 를 상태 추정치로 갱신한다.
        innovation = dobi - self._a
        gain = self._p / (self._p + self.var_err)
        self._p = gain * self.var_err + self.var_sig
        self._a = self._a + gain * innovation

        self._values["imbalance"] = imbalance
        self._values["dobi"] = dobi
        self._values["filtered"] = self._a
        return self._a
