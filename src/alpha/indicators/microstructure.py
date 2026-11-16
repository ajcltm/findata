"""
═══════════════════════════════════════════════════════════════════
 microstructure.py — 호가·체결 미시구조 지표
═══════════════════════════════════════════════════════════════════

■ 무엇을 담았나
    예전 DOBI 전략(2020년 pandas 스크립트)에서 실제로 값을 하던 계산을
    증분 형태로 옮기고, 그때 있던 두 가지 오류를 고쳤다.

        ① 매수 쪽 2호가 참조 오타 (prev_b2 자리에 매수1차선잔량)
        ② 최우선호가 안쪽에 새 호가가 생겼을 때의 부호

    ②는 설명이 필요하다. 매도1호가가 15,000 → 14,950 으로 내려갔다는 건
    스프레드 안쪽에 새 매도물량이 들어온 것이다. 매도 압력이다. 그런데
    예전 코드는 이걸 net = curr_a1 (양수 = 매도벽 소진 = 상승 신호)로
    계산했다. 부호가 반대였다. 여기서는 -curr_a1 로 잡는다.
    (Cont-Kukanov-Stoikov 의 OFI 정의와 같은 방향)

■ 시간 기준 — 왜 period 대신 초를 쓰나
    "300틱 전"은 종목마다 시간이 다르다. H0STASP0 은 이벤트가 아니라
    주기적 스냅샷이고, 밀도가 종목·시간대·유량제한에 따라 달라진다.
    삼성전자의 300 스냅샷과 중소형주의 300 스냅샷은 같은 값이 아니다.

    그래서 이 파일의 스무딩·누적은 전부 초 단위다. TimeEMA(30)은
    종목이 바뀌어도 "30초 반감기"라는 같은 의미를 유지한다.

    리샘플링을 따로 하지 않는 것도 의도다. 리샘플링하면 백테스트에는
    resample() 이, 실전에는 버킷 누적 로직이 따로 생긴다 — indicators.py
    도입부에서 피하려던 바로 그 상황이다. 이벤트가 올 때마다 갱신하되
    가중치를 Δt 로 주면 두 경로가 같은 코드로 돌아간다.

■ ★ dt 가 초 단위라는 제약 — 지금은 recv_dt 로 해결됐다 ★
    parse_hhmmss 가 "093015" 를 파싱하므로 MarketEvent.dt(거래소 시각)의
    최소 단위는 1초다. 같은 초에 스냅샷이 여러 개 오면 Δt = 0 이 되고,
    그대로 두면 k = 0 이라 그 이벤트들이 통째로 무시된다.

    이 파일을 처음 짤 때는 이 문제가 해결 전이라 min_dt 로 하한을 둬서
    흡수하는 임시방편만 있었다(아래 그 흔적이 남아있다). 그 사이
    events.py 에 MarketEvent.recv_dt(소켓에서 실제로 수신·파싱한 벽시계
    시각, 마이크로초 단위)가 추가됐다 — _event_time() 이 이제 그걸
    우선 쓴다. min_dt 는 recv_dt 마저 없는 경로(백테스트 재생 등)를
    위한 안전망으로만 남겨뒀다.

■ 합치지 않았다
    강도(QueueImbalance)와 속도(BestQuoteFlow)를 하나의 score 로 묶는
    지표는 일부러 만들지 않았다. 단위가 다르고, 개별 예측력을 모르는
    상태에서 가중치를 정하면 그건 노이즈를 학습하는 것이다.
    각각 뽑아서 수익률과 따로 비교한 뒤에 합칠지 정하는 게 순서다.

■ ★ TradeFlowImbalance 를 쓰기 전에 events.py 의 _EXEC_SIDE 를 확인할 것 ★
    자세한 것은 그 클래스 주석 참조. 부호가 뒤집혀 있어도 백테스트는
    조용히 잘 돌아간다.

■ 등록 예
        self.flow  = self.ind(BestQuoteFlow(),                    on="quote")
        self.ofi   = self.ind(TimeEMA(30, self.flow, "ofi_norm"), on="quote",
                              name="ofi_30s")
        self.imb   = self.ind(QueueImbalance(levels=3),           on="quote")
        self.imb_s = self.ind(TimeEMA(30, self.imb),              on="quote")
        self.dep   = self.ind(DepletionRate(self.flow, 60),       on="quote")
        self.tfi   = self.ind(TradeFlowImbalance(30),             on="tick")

    (실제 이 프로젝트에서의 등록은 alpha/strategy/microstructure_watcher.py
     참고 — 종목별 인스턴스 분리, 뷰/저장 구독까지 거기서 끝난다.)

    ★ 등록 순서 ★ TimeEMA/TimeSum/DepletionRate/KalmanTrend 는 입력
    지표보다 반드시 뒤에 와야 한다 (CrossOver 와 같은 규칙).

    ★ name= 을 주는 편이 낫다 ★ 자동 이름은 입력 지표까지 펼쳐져
    "TimeEMA(30,source=BestQuoteFlow(),line=ofi_norm)" 처럼 길어진다.
    기록 라벨로 쓸 거면 짧게 지어주는 게 편하다.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Optional

from alpha.indicators.indicators import Indicator


# ═══════════════════════════════════════════════════════════
# 공통 유틸
# ═══════════════════════════════════════════════════════════

def _event_time(ev) -> float:
    """이벤트 시각을 초(float)로. recv_dt 가 있으면 그걸 우선한다.

    MarketEvent.dt 는 parse_hhmmss 산물이라 초 단위까지만 있다.
    MarketEvent.recv_dt(소켓 수신 벽시계, 마이크로초 있음)가 있으면
    그쪽이 훨씬 정밀하므로 우선 쓴다 — 백테스트 재생처럼 recv_dt 가
    없는 경로에서만 dt 로 폴백한다."""
    recv = getattr(ev, "recv_dt", None)
    if recv is not None:
        return recv.timestamp()
    return ev.dt.timestamp()


def _ladder(quote, side: str) -> dict:
    """가격 -> 잔량 사전. 직전 사다리에서 특정 가격의 잔량을 찾을 때 쓴다.

    가격 0 은 제외한다 — 장 시작 전에는 KIS 가 호가를 전부 0 으로 보내고,
    _f() 가 이를 0.0 으로 파싱한다. 그대로 두면 '0원 호가'가 사다리에
    섞여 유출량 계산이 망가진다."""
    prices = (quote.asks if side == "ask" else quote.bids) or ()
    sizes = (quote.ask_sizes if side == "ask" else quote.bid_sizes) or ()
    return {p: float(q) for p, q in zip(prices, sizes) if p and p > 0}


# ═══════════════════════════════════════════════════════════
# 시간 기반 평활 — 틱 개수가 아니라 초로 센다
# ═══════════════════════════════════════════════════════════

class TimeEMA(Indicator):
    """반감기를 '초'로 주는 지수이동평균.

    ■ 일반 EMA 와 무엇이 다른가
        EMA(50) 은 k = 2/51 로 고정이다. 이벤트가 초당 1개 오든 100개
        오든 같은 가중치를 준다. 스냅샷 밀도가 변하는 호가 데이터에서는
        같은 파라미터가 시간대마다 다른 평활을 하게 된다.

        여기서는 Δt 로 가중치를 계산한다:
            k = 1 - exp(-Δt / τ),   τ = halflife / ln2

        Δt 가 크면(오랜만에 온 이벤트) k 가 1에 가까워 새 값을 크게
        반영하고, 짧으면 조금만 반영한다. 이벤트가 몰려 와도 '30초
        반감기'라는 의미가 유지된다.

    ■ 입력
        source 를 주면 그 지표의 line(line) 값을 읽고,
        안 주면 이벤트의 src 필드를 읽는다.
            TimeEMA(30, flow, "ofi_norm")     지표 라인
            TimeEMA(30, src="spread")         이벤트 필드(Tick/Quote 둘 다 있다)

    ■ min_dt
        recv_dt 가 없는 경로(백테스트 재생 등)에서는 dt 가 초 단위라
        같은 초의 이벤트는 Δt = 0 이 된다. 그대로면 k = 0 이라 무시되므로
        하한을 둔다. 기본 0.1 은 '초당 10건 정도'라는 가정이고, 종목
        밀도를 보고 조정할 것. recv_dt 가 있는 실전/모의에서는 사실상
        안 쓰인다(마이크로초 단위라 Δt = 0 이 거의 안 생긴다).

    ■ warmup
        개수로 셀 수 없어 None 이다. 대신 첫 값이 들어온 뒤 halflife 의
        3~5배 시간이 지나야 값이 안정된다고 보면 된다.

    ■ ★ 중복 스냅샷 — 상류의 is_dup 플래그를 본다 ★
        source 가 BestQuoteFlow 처럼 is_dup 라인을 내는 지표면, 중복일
        때는 건드리지 않고 직전 값을 그대로 낸다(dup_line 파라미터,
        alpha.indicators.kalman.KalmanTrend 와 같은 계약). source 에
        그 라인이 없으면(None 반환) 그냥 통과한다 — 중복 판정이 없는
        지표(QueueImbalance 등)를 소스로 쓸 때는 영향이 없다.
    """

    def __init__(self, halflife: float, source: Optional[Indicator] = None,
                 line: str = "value", src: Optional[str] = None,
                 min_dt: float = 0.1, dup_line: Optional[str] = "is_dup"):
        if source is None and src is None:
            raise ValueError("source 또는 src 중 하나는 있어야 한다")
        self.halflife = halflife
        self.source = source
        self.line_name = line
        self.src = src
        self.min_dt = min_dt
        self.dup_line = dup_line
        self.tau = halflife / math.log(2.0)
        self._prev_t: Optional[float] = None

    @property
    def warmup(self) -> Optional[int]:
        return None                              # 시간 기준이라 개수로 못 센다

    def update(self, ev):
        if (self.source is not None and self.dup_line
                and self.source.line(self.dup_line)):
            return self.line("value")            # 중복 스냅샷 — 값 유지

        x = (self.source.line(self.line_name) if self.source is not None
             else getattr(ev, self.src, None))
        if x is None:
            return self.line("value")            # 입력이 아직 없다 — 값 유지

        t = _event_time(ev)
        cur = self._values.get("value")

        if cur is None or self._prev_t is None:
            cur = float(x)                       # 첫 값이 곧 시드
        else:
            dt = max(t - self._prev_t, self.min_dt)   # 시각 역전도 여기서 흡수
            k = 1.0 - math.exp(-dt / self.tau)
            cur = float(x) * k + cur * (1.0 - k)

        self._prev_t = t
        self._values["value"] = cur
        return cur


class TimeSum(Indicator):
    """최근 N초 동안의 합계. 슬라이딩 시간창.

    ■ 왜 rolling(60).sum() 이 아닌가
        같은 이유다 — 60개 이벤트가 종목마다 다른 시간을 뜻한다.
        여기서는 창 밖으로 나간 값을 시각 기준으로 뺀다.

    ■ 유량(flow) 전용이다
        순유출량처럼 '구간 동안 벌어진 일의 양'에는 합계가 맞다.
        잔량 같은 '상태'에는 쓰면 안 된다 — 그건 마지막 값을 봐야 한다.
        (예전 pandas 코드로 치면 flow 는 resample().sum(),
         depth 는 resample().last() 인 것과 같은 구분이다)

    ■ ★ 중복 스냅샷 — 상류의 is_dup 플래그를 본다 ★
        유량(flow) 전용이라는 위 설명 때문에 이게 특히 중요하다. 중복
        스냅샷에서는 BestQuoteFlow 가 ask_flow/bid_flow 를 직전 값
        그대로 들고 있을 뿐 '새로 유출된 물량'이 아니다. 여기서
        걸러주지 않으면 같은 유출량이 매 중복 틱마다 또 합산돼 창 합계가
        중복 횟수만큼 부풀어 오른다(DepletionRate 가 바로 이 클래스를
        쓴다). dup_line 이 가리키는 라인이 참이면 통째로 건너뛴다 —
        버퍼도 합계도 커서(cutoff)도 그 틱 기준으로는 움직이지 않는다.
    """

    def __init__(self, window: float, source: Optional[Indicator] = None,
                 line: str = "value", src: Optional[str] = None,
                 dup_line: Optional[str] = "is_dup"):
        if source is None and src is None:
            raise ValueError("source 또는 src 중 하나는 있어야 한다")
        self.window = window
        self.source = source
        self.line_name = line
        self.src = src
        self.dup_line = dup_line
        self.buf: deque = deque()                # (시각, 값)
        self._sum = 0.0

    @property
    def warmup(self) -> Optional[int]:
        return None

    def update(self, ev):
        if (self.source is not None and self.dup_line
                and self.source.line(self.dup_line)):
            return self.line("value")            # 중복 스냅샷 — 합계 유지

        x = (self.source.line(self.line_name) if self.source is not None
             else getattr(ev, self.src, None))
        if x is None:
            return self.line("value")

        t = _event_time(ev)
        self.buf.append((t, float(x)))
        self._sum += float(x)

        cutoff = t - self.window
        while self.buf and self.buf[0][0] < cutoff:
            self._sum -= self.buf.popleft()[1]

        self._values["value"] = self._sum
        return self._sum


# 예전 KalmanTrend(2상태 칼만, q 고정 비율·R 만 온라인 추정)는
# legacy/microstructure_kalman.py 로 옮겼다. 실전 등록은
# alpha.indicators.kalman.KalmanTrend 를 쓴다.


# ═══════════════════════════════════════════════════════════
# 호가 흐름 — '속도' 축
# ═══════════════════════════════════════════════════════════

class BestQuoteFlow(Indicator):
    """최우선호가 순유출량. 예전 dobi_1 의 수정판.

    ■ 라인 (대표 라인 없음 — MACD 와 같은 이유)
        ask_flow    매도 최우선호가에서 순수하게 빠져나간 물량.
                    양수 = 매도벽이 먹히고 있다(상승 압력)
        bid_flow    매수 쪽 같은 값.
                    양수 = 매수벽이 먹히고 있다(하락 압력)
        ofi         ask_flow - bid_flow. 양수 = 매수 압력. 단위는 '주'.
        ofi_norm    ofi 를 직전 최우선호가 잔량 합으로 나눈 값.
                    종목·시간대가 달라도 비교 가능하다. 보통 이걸 쓴다.
        truncated   1 이면 이번 값의 신뢰도가 낮다(아래 참조)
        is_dup      1 이면 이번 스냅샷이 중복이고 위 값들은 **직전 행의
                    반복**이다(새 측정이 아니다). 하류와 분석이 이걸 본다
        dup_frac    중복 스냅샷 누적 비율. ★ 녹음에서 제일 먼저 볼 것 ★

    ■ 계산 — 가격이 움직일 때가 핵심이다
        단순히 curr_qty - prev_qty 를 하면 안 된다. 매도1호가가
        15,000 → 15,050 으로 올라갔다면 두 잔량은 아예 다른 가격의
        물량이라 빼도 의미가 없다. 그리고 이 순간이 정보가 가장 많다.

        그래서 직전 사다리 전체를 사전으로 들고 가격으로 대조한다:

        매도 최우선호가가
          ├ 올라갔다   사라진 가격대들의 직전 잔량 전부 소진
          │            + (새 가격의 직전 잔량 - 현재 잔량)
          ├ 그대로     직전 잔량 - 현재 잔량
          └ 내려갔다   -현재 잔량   ← 스프레드 안쪽에 새 매도물량 유입.
                                     예전 코드가 +로 잘못 잡던 부분

        매수는 부등호만 뒤집으면 대칭이다.

        예전 코드는 '올라갔다'를 prev_a2 + prev_a1 - curr_a1 로 계산해
        2호가까지만 봤다. 가격으로 대조하면 몇 칸을 뛰어넘어도 맞고,
        호가 단위가 바뀌는 가격대에서도 안전하다.

    ■ 사다리 밖으로 나간 경우 (truncated)
        가격이 10호가 범위를 통째로 뛰어넘으면(급등락, VI 해제 직후)
        직전 사다리에 대응 가격이 없다. 그때는 보이던 물량만 소진으로
        잡을 수밖에 없어 값이 과소평가된다. truncated 라인을 1 로 세우니
        분석할 때 이 구간을 걸러낼 것.

    ■ ★ 중복 스냅샷 — 여기서 걸러서 None 을 낸다 ★
        H0STASP0 은 호가가 안 바뀌어도 주기적으로 온다. 그때
        '사다리가 하나도 안 변한' 스냅샷이 들어온다.

        걸러야 하는 이유 — 0 과 구별이 안 된다:
            호가창 그대로        → ofi = 0  "정보가 없다"
            순유출이 실제로 0    → ofi = 0  "진짜 측정값"
        안 걸러내면 둘이 같은 숫자로 하류에 흘러간다. 특히 2차 차분을
        쓰는 추정기에서는 세 점 중 둘이 같아지면 2차 차분이 1차 차분으로
        퇴화한다 (z₁=z₀ → D = h₁(z₂-z₀), 기울기 소거가 깨짐).

        판정은 **이 클래스가 읽는 필드 전부**를 비교한다:
            best_ask, best_bid, ask_sizes[0], bid_sizes[0], 두 사다리
        · 가격만 비교하면 안 된다 — 가격 고정에 잔량만 변하는 게 가장
          흔하고 그게 주 신호다 (ask1 500→300 이면 ask_flow = 200).
        · 최우선호가 4개만 비교하면 안 된다 — 깊은 호가가 변했는데
          건너뛰면 _prev_*_ladder 가 낡아서 다음 계산의 gone 합이 틀린다.
        · 전부 같으면 갱신할 것도 내보낼 것도 없다. 그래서 안전하다.

        깊은 호가만 바뀐 경우는 중복이 아니다 — 최우선호가 순유출이
        진짜로 0 이므로 ofi_norm = 0 을 내보내고 사다리는 갱신한다.

        ▶ ★ 중복일 때 값 라인을 건드리지 않는다 ★ update 호출마다
          자동 레코딩되므로 None 을 넣으면 기록에 None 행이 쌓여
          이 지표의 시계열 자체가 망가진다. 대신 is_dup = 1 만 세운다.
          의미상으로도 맞다 — 호가창이 안 변했으니 마지막 측정값이
          여전히 현재 상태이고, '새 측정이 아니다'라는 표시만 필요하다.
        ▶ 하류(칼만)는 is_dup 을 읽고 갱신을 건너뛴다. 호출 순서가
          보장이다 — 같은 quote 이벤트에서 이 지표가 먼저 갱신된다.
        ▶ 분석할 때는 is_dup == 0 인 행만 쓰면 된다. 무손실이다.
        ▶ dup_frac 으로 누적 비율을 본다.

    ■ 스냅샷의 한계
        H0STASP0 은 이벤트가 아니라 스냅샷이다. 두 스냅샷 사이에
        체결과 취소가 여러 번 있었어도 순변화 하나로만 보인다.
        여기서 나오는 값은 '주문 흐름'이 아니라 '스냅샷 간 순유출'이다.
        원리적 한계이므로 해석할 때 감안할 것.

    ■ ★ 미해결: 분모가 분자와 안 맞는다 ★
        denom 은 직전 **최우선호가** 잔량 합(ask1+bid1)인데, 분자 ofi 는
        가격이 밀리면 **여러 호가의 물량을 통째로** 삼킨다(gone 합).
        그래서 급변 구간에서 ofi_norm 이 1 을 크게 넘을 수 있고, 종목 간
        스케일이 벌어진다(측정: obs_var 종목 간 597배, 003550 의
        level ±1.79). 이 클래스의 알려진 문제이고 아직 안 고쳤다.

    ■ Tick 으로도 만들 수 있다
        Tick 은 bid/ask/bid_size/ask_size(1호가)를 같이 싣고 온다.
        체결이 날 때마다 갱신되므로 타이밍이 더 좋을 수 있지만, 사다리가
        1호가뿐이라 '가격이 밀렸을 때'의 보정을 못 한다. 그래서 이
        클래스는 Quote 전용으로 뒀다. 비교해볼 가치는 있다.

    등록: self.ind(BestQuoteFlow(), on="quote", symbol=sym)
    조회: .line("ofi_norm") 등  (.value 는 라인이 여럿이라 항상 None)
    """

    def __init__(self):
        self._prev_ask_p = None
        self._prev_bid_p = None
        self._prev_ask_ladder: dict = {}
        self._prev_bid_ladder: dict = {}
        self._prev_top_qty = 0.0                 # 정규화 분모
        self._prev_key = None                    # 중복 판정용
        self._n_total = 0
        self._n_dup = 0

    @property
    def warmup(self) -> Optional[int]:
        return 2                                 # 직전 사다리가 있어야 한다

    @staticmethod
    def _side_flow(prev_p, curr_p, curr_q, prev_ladder, is_ask: bool):
        """한쪽 사다리의 순유출량. 반환: (유출량, 사다리를 벗어났는가)

        is_ask 면 가격이 '올라가는' 쪽이 소진이고, 매수는 반대다."""
        if curr_p == prev_p:
            return prev_ladder.get(curr_p, 0.0) - curr_q, False

        moved_away = (curr_p > prev_p) if is_ask else (curr_p < prev_p)

        if not moved_away:
            # 스프레드 안쪽에 새 호가가 생겼다 = 유입. 유출의 반대 부호.
            return -curr_q, False

        # 최우선호가가 밀려났다 = 앞쪽 가격대가 소진됐다.
        gone = sum(q for p, q in prev_ladder.items()
                   if (p < curr_p if is_ask else p > curr_p))

        if curr_p in prev_ladder:
            return gone + prev_ladder[curr_p] - curr_q, False

        # 직전 사다리를 통째로 건너뛰었다. 보이던 물량까지만 셀 수 있다.
        return gone, True

    def update(self, quote):
        ask_p, bid_p = quote.best_ask, quote.best_bid
        if not ask_p or not bid_p:
            return None                          # 한쪽이 비었다/장 전 — 보류

        ask_q = float(quote.ask_sizes[0]) if quote.ask_sizes else 0.0
        bid_q = float(quote.bid_sizes[0]) if quote.bid_sizes else 0.0

        # 사다리를 먼저 만든다 — 중복 판정과 _remember 가 같은 것을 쓴다
        # (전에는 _remember 안에서 만들었다. 중복 비교에 필요해서 올렸다)
        ask_ladder = _ladder(quote, "ask")
        bid_ladder = _ladder(quote, "bid")

        # ── ★ 중복 스냅샷 ★ 이 클래스가 읽는 필드 전부를 비교한다 ──
        #   가격만 / 최우선호가만 비교하면 안 되는 이유는 클래스 docstring.
        key = (ask_p, bid_p, ask_q, bid_q,
               tuple(sorted(ask_ladder.items())),
               tuple(sorted(bid_ladder.items())))
        self._n_total += 1
        if key == self._prev_key:
            self._n_dup += 1
            # ★ 값 라인을 건드리지 않는다 ★ 자동 레코딩이므로 None 을
            #   넣으면 기록이 왜곡된다. 플래그만 세우고 그대로 나간다.
            self._values["is_dup"] = 1.0
            self._values["dup_frac"] = self._n_dup / self._n_total
            return self.line("ofi")
        self._prev_key = key
        self._values["is_dup"] = 0.0

        if self._prev_ask_p is None:
            # 첫 호가는 비교 대상이 없다. 사다리만 기록하고 넘어간다
            # (CrossOver 의 '첫 계산은 차이만 기록'과 같은 규칙).
            self._remember(ask_p, bid_p, ask_q, bid_q, ask_ladder, bid_ladder)
            self._values["dup_frac"] = self._n_dup / self._n_total
            return None

        ask_flow, a_trunc = self._side_flow(
            self._prev_ask_p, ask_p, ask_q, self._prev_ask_ladder, is_ask=True)
        bid_flow, b_trunc = self._side_flow(
            self._prev_bid_p, bid_p, bid_q, self._prev_bid_ladder, is_ask=False)

        ofi = ask_flow - bid_flow
        denom = self._prev_top_qty
        ofi_norm = ofi / denom if denom > 0 else None

        self._remember(ask_p, bid_p, ask_q, bid_q, ask_ladder, bid_ladder)

        self._values["ask_flow"] = ask_flow
        self._values["bid_flow"] = bid_flow
        self._values["ofi"] = ofi
        self._values["ofi_norm"] = ofi_norm
        self._values["truncated"] = 1.0 if (a_trunc or b_trunc) else 0.0
        self._values["dup_frac"] = self._n_dup / self._n_total
        return ofi

    def _remember(self, ask_p, bid_p, ask_q, bid_q, ask_ladder, bid_ladder):
        self._prev_ask_p, self._prev_bid_p = ask_p, bid_p
        self._prev_ask_ladder = ask_ladder
        self._prev_bid_ladder = bid_ladder
        self._prev_top_qty = ask_q + bid_q


class DepletionRate(Indicator):
    """상대 소진 속도. 유출량을 현재 잔량으로 나눈다.

    ■ 왜 나누나
        "매도1호가에서 3,000주가 빠졌다"는 그 자리에 10,000주가 있을 때와
        3,500주가 있을 때 의미가 전혀 다르다. 후자는 곧 벽이 무너진다.
        예전 코드의 relative_velocity 가 이 발상이었다.

    ■ 라인
        ask   최근 N초 매도측 유출 / 현재 매도1호가 잔량.
              1.0 을 넘으면 지금 남은 물량만큼이 그 시간 안에 이미 빠졌다는 뜻.
        bid   매수측 같은 값
        net   ask - bid. 양수 = 매도벽이 상대적으로 빨리 무너지는 중

    ■ 분모 주의
        잔량이 아주 작아지면 값이 발산한다. min_qty 미만이면 None 을
        낸다 — 여기서 큰 값이 나오는 건 신호가 아니라 0으로 나눈 것에
        가깝다.

    등록: self.ind(DepletionRate(self.flow, 60), on="quote")   ← flow 뒤에
    """

    def __init__(self, flow: BestQuoteFlow, window: float = 60.0,
                 min_qty: float = 1.0):
        self.flow = flow
        self.window = window
        self.min_qty = min_qty
        self.ask_sum = TimeSum(window, flow, "ask_flow")
        self.bid_sum = TimeSum(window, flow, "bid_flow")

    @property
    def warmup(self) -> Optional[int]:
        return None

    def update(self, quote):
        a_sum = self.ask_sum.update(quote)
        b_sum = self.bid_sum.update(quote)
        if a_sum is None or b_sum is None:
            return None

        ask_q = float(quote.ask_sizes[0]) if quote.ask_sizes else 0.0
        bid_q = float(quote.bid_sizes[0]) if quote.bid_sizes else 0.0

        ask = a_sum / ask_q if ask_q >= self.min_qty else None
        bid = b_sum / bid_q if bid_q >= self.min_qty else None

        self._values["ask"] = ask
        self._values["bid"] = bid
        self._values["net"] = None if (ask is None or bid is None) else ask - bid
        return self._values["net"]


# ═══════════════════════════════════════════════════════════
# 호가 잔량 — '강도' 축
# ═══════════════════════════════════════════════════════════

class QueueImbalance(Indicator):
    """호가 잔량 불균형. -1(매도 우세) ~ +1(매수 우세).

    ■ Quote.imbalance 와의 관계
        events.py 의 Quote.imbalance 는 total_bid_size/total_ask_size,
        즉 호가창 전체 잔량으로 계산한다. 이 지표는 levels 로 깊이를
        고른다. levels=0 이면 Quote.imbalance 와 같은 값이 된다.

    ■ 예전 전략에 없던 축이다
        예전 코드는 잔량의 '변화'만 봤고 '수준'은 진입 조건에서 빠져
        있었다(ask_5_q_bid_5_q_* 를 만들어놓고 주석 처리한 흔적이 있다).
        이건 그 축을 다시 세우는 것이므로, 복원이 아니라 추가다.

    ■ 비율(A/B) 대신 정규화를 쓰는 이유
            I = (B - A) / (B + A)
        A/B 는 한쪽 잔량이 작아지면 발산한다. 매수잔량 100주짜리
        스냅샷 하나가 수백의 값을 만들고, 평활에 넣으면 그 한 틱이
        이후 수백 초를 오염시킨다.
        I 는 [-1, 1] 에 갇히고, 부호가 방향과 직접 대응하고, 종목이
        달라도 임계값을 다시 찾을 필요가 없다.

    ■ levels
        1 이면 최우선호가만, 3 이면 3호가까지 누적, 0 이면 전체.
        1호가만 보면 스푸핑(걸었다 빼는 주문)에 취약하고, 깊게 보면
        멀리 있는 체결 안 될 물량까지 섞인다. 둘 다 만들어 비교할 것.

    등록: self.ind(QueueImbalance(levels=3), on="quote", symbol=sym)
    조회: .value  (라인이 하나라 자동으로 잡힌다)
    """

    def __init__(self, levels: int = 1):
        self.levels = levels

    @property
    def warmup(self) -> Optional[int]:
        return 1                                 # 스냅샷 하나면 값이 나온다

    def update(self, quote):
        if self.levels <= 0:
            a = float(quote.total_ask_size)
            b = float(quote.total_bid_size)
        else:
            n = self.levels
            a = float(sum(quote.ask_sizes[:n]))
            b = float(sum(quote.bid_sizes[:n]))

        total = a + b
        if total <= 0:
            return self.line("value")            # 양쪽 다 비었다 — 값 유지

        value = (b - a) / total
        self._values["value"] = value
        return value


# ═══════════════════════════════════════════════════════════
# 체결 흐름 — 예전 c_ask_c_bid 의 대체
# ═══════════════════════════════════════════════════════════

class TradeFlowImbalance(Indicator):
    """공격적 매수/매도 체결량의 불균형. -1 ~ +1.

    ■ TickImbalance 와 다르다
        그쪽은 체결 '건수'를 센다. 이건 '수량'으로 가중한다.
        1주짜리 체결 100건과 10만주 체결 1건은 의미가 다르다.

    ■ 예전 c_ask_c_bid 를 대체한다
        예전 코드는 Kalman(매도체결량) / Kalman(매수체결량) 의 비율이었고
        임계값이 2였다. 비율이라 분모가 작아지면 발산하는 문제가 같아서
        여기서도 정규화 형태로 바꿨다.
            +1 에 가까움 = 공격적 매수가 시장을 주도
            임계값 2 (비율)  ≈  +0.33 (정규화)

    ■ ★ events.py 의 _EXEC_SIDE 를 확인할 것 ★
        지금 events.py 는 이렇게 매핑한다.

            _EXEC_SIDE = {"1": "sell", "5": "buy"}
            # 주석: "KIS 는 1=매도체결, 5=매수체결로 보낸다"

        그런데 KIS 문서의 CCLD_DVSN 은 1:매수(+), 3:장전, 5:매도(-) 다.
        반대로 보인다. 확인 없이 쓰면 이 지표의 부호가 통째로 뒤집히고,
        백테스트는 아무 오류 없이 돌아간다.

        데이터로 확인하는 게 가장 확실하다. Tick 은 체결가와 1호가를
        같이 싣고 오므로 파싱된 side 와 실제 체결 위치를 대조할 수 있다.

            # 매수 주도면 매도호가를 때린 것이므로 price >= ask 여야 한다
            agree = sum(1 for t in ticks
                        if t.side == "buy" and t.ask and t.price >= t.ask)
            flip  = sum(1 for t in ticks
                        if t.side == "buy" and t.bid and t.price <= t.bid)

        flip 이 압도적이면 매핑이 뒤집힌 것이다. 고칠 곳은 events.py 의
        _EXEC_SIDE 한 줄이고, 이 클래스는 side 문자열만 보므로 자동으로
        따라온다.

        ※ Tick 의 bid/ask 는 체결 '직후' 갱신된 호가일 수 있어 경계에서
          어긋나는 건이 섞인다. 압도적 다수가 어느 쪽인지로 판단할 것.

    ■ 창 밖 처리
        최근 N초의 체결만 본다. 예전 코드의 칼만(≈530틱)과 달리 창이
        명시적이라, IC 곡선에서 나온 시간축을 그대로 넣으면 된다.

    등록: self.ind(TradeFlowImbalance(30), on="tick", symbol=sym)
    """

    def __init__(self, window: float = 30.0):
        self.window = window
        self.buf: deque = deque()                # (시각, 매수량, 매도량)
        self._buy = 0.0
        self._sell = 0.0

    @property
    def warmup(self) -> Optional[int]:
        return None

    def update(self, ev):
        side = ev.side
        qty = float(ev.volume or 0.0)
        if side not in ("buy", "sell") or qty <= 0:
            return self.line("value")            # 방향 불명/장전 — 값 유지

        t = _event_time(ev)
        buy = qty if side == "buy" else 0.0
        sell = qty if side == "sell" else 0.0

        self.buf.append((t, buy, sell))
        self._buy += buy
        self._sell += sell

        cutoff = t - self.window
        while self.buf and self.buf[0][0] < cutoff:
            _, b, s = self.buf.popleft()
            self._buy -= b
            self._sell -= s

        total = self._buy + self._sell
        if total <= 0:
            return self.line("value")

        value = (self._buy - self._sell) / total
        self._values["value"] = value
        return value
