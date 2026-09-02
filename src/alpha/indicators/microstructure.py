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
    """

    def __init__(self, halflife: float, source: Optional[Indicator] = None,
                 line: str = "value", src: Optional[str] = None,
                 min_dt: float = 0.1):
        if source is None and src is None:
            raise ValueError("source 또는 src 중 하나는 있어야 한다")
        self.halflife = halflife
        self.source = source
        self.line_name = line
        self.src = src
        self.min_dt = min_dt
        self.tau = halflife / math.log(2.0)
        self._prev_t: Optional[float] = None

    @property
    def warmup(self) -> Optional[int]:
        return None                              # 시간 기준이라 개수로 못 센다

    def update(self, ev):
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
    """

    def __init__(self, window: float, source: Optional[Indicator] = None,
                 line: str = "value", src: Optional[str] = None):
        if source is None and src is None:
            raise ValueError("source 또는 src 중 하나는 있어야 한다")
        self.window = window
        self.source = source
        self.line_name = line
        self.src = src
        self.buf: deque = deque()                # (시각, 값)
        self._sum = 0.0

    @property
    def warmup(self) -> Optional[int]:
        return None

    def update(self, ev):
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


class KalmanTrend(Indicator):
    """2상태 칼만 필터 — 수준(level)과 기울기(slope)를 함께 추정한다.
 
    ■ 예전 코드의 칼만과 무엇이 다른가
        예전 것은 상태가 '수준' 하나뿐인 local level model 이었다.
        그 모델은 정상상태에서 게인이 상수로 수렴하고, 그 순간
 
            a_t = k·z_t + (1-k)·a_{t-1}
 
        즉 EMA 와 완전히 같은 식이 된다. 실제로 var_err=7e6, var_sig=100
        이면 게인이 약 0.0038 로 수렴해 span 530 EMA 와 같았다.
        '칼만이라서' 빠른 게 아니라 그냥 EMA 였다.
 
        여기서는 상태를 두 개로 늘린다:
 
            x = [level, slope]        F = [[1, Δt],
                                           [0,  1]]
 
        값이 일정한 속도로 움직이는 구간에서 EMA 는 구조적으로 뒤처진다
        (과거의 평균이므로). 이 모델은 '지금 초당 얼마씩 움직이는 중'을
        상태로 들고 있어서 그 지연이 원리적으로 사라진다. 이게 EMA 로는
        흉내낼 수 없는 차이다.
 
    ■ 라인
        level    평활된 수준. TimeEMA 의 자리를 대신한다
        slope    초당 변화율. GPT 가 말한 '속도' 축이 여기 있다.
                 단순 차분과 달리 잡음이 걸러진 추정치다
        lead     level + slope × lead_s. lead_s 초 뒤의 외삽값.
                 lead_s=0 이면 level 과 같다
        obs_var  현재 잡음 분산 추정치. 종목 간 비교와 수렴 확인용
        nis      정규화 innovation 제곱. 평균이 1 근처여야 한다(아래)
 
    ■ 파라미터 — 하나만 정하면 된다
        q          기울기가 변하는 정도를 obs_var 대비 비율로 준다.
                   Q = q × obs_var. 클수록 추세 변화를 빨리 따라가고,
                   작을수록 매끄럽지만 굼뜨다.
                   ★ q = 0 이면 예전 1상태 필터와 같아진다 ★
                   2상태가 실제로 이득인지 판정하는 대조군이므로
                   실험할 때 반드시 포함할 것.
        obs_var    관측 잡음의 분산. None 이면 데이터에서 스스로 추정한다
                   (아래 참조). 아는 값이 있으면 넣어서 고정할 수 있다.
 
        원래 칼만은 obs_var 와 trend_var 두 개를 요구하지만, 필터의
        성격을 정하는 건 둘의 비율 하나다. 절대값을 열 배씩 키워도
        비율이 같으면 결과가 같다. 그래서 여기서는 비율(q)만 노출하고
        척도(obs_var)는 데이터가 정하게 한다.
 
        예전 코드에서 var_err=7e6 이 dobi_1(수천 단위)에는 그럴듯했다가
        정규화된 입력(±0.05)에는 열 자릿수가 어긋났던 것도 이 척도
        문제였다. 비율만 맞으면 돌아가긴 하지만 파라미터의 의미가
        사라진다.
 
    ■ obs_var 온라인 추정 — 종목마다 따로 재지 않아도 된다
        대형주와 소형주는 ofi_norm 의 잡음 수준이 자릿수로 다르다.
        종목별로 미리 재서 넣는 건 현실적이지 않으므로 필터가 직접 잡는다.
 
        연속한 두 관측의 차이를 쓴다:
 
            Var(z_t - z_{t-1}) ≈ 2·R      (느린 신호 성분은 상쇄되고
                                           독립 잡음만 남으므로)
 
        그래서 (Δz)²/2 를 시간 기반 EMA 로 누적한 값이 R 추정치다.
 
        ★ 이 방식은 열린 루프다 ★ 필터 상태를 안 쓰고 관측만 본다.
        innovation 으로 추정하는 고전적 적응 칼만(Mehra 방식)도 있지만,
        그건 R 추정이 필터에 영향을 주고 필터가 다시 R 추정에 영향을 주는
        닫힌 루프라 발산할 수 있다. 여기서는 안정성을 택했다.
        innovation 은 대신 진단용(nis 라인)으로만 쓴다.
 
        주의: Δt 가 길면 그 사이 진짜 신호도 움직여서 R 이 과대추정된다.
        max_pair_dt 보다 간격이 벌어진 쌍은 추정에서 제외한다.
 
    ■ 진단 — nis 라인을 보라
        nis = innovation² / S. 필터가 자기 예측 오차를 제대로 알고
        있다면 이 값의 평균이 1 근처여야 한다.
            평균 ≫ 1  필터가 자기를 과신하는 중 → q 를 키울 것
            평균 ≪ 1  과하게 불신하는 중 → q 를 줄일 것
        하루치 평균을 찍어보면 q 가 자릿수라도 맞는지 바로 안다.
        IC 를 돌리기 전에 이걸로 먼저 거르면 탐색 범위가 줄어든다.
 
    ■ Δt 를 원래 다룬다
        프로세스 잡음이 Δt 에 비례하는 게 모델의 일부다(등가속도 모델의
        표준형). TimeEMA 처럼 시간 가중을 덧붙인 게 아니라 원래 그렇게
        생겼다. 스냅샷 밀도가 변해도 파라미터의 의미가 유지된다.
        다만 dt 가 초 단위인 문제는 그대로이므로 min_dt 는 여기도 있다.
 
    ■ ★ 외삽은 공짜가 아니다 ★
        lead_s 를 키우면 지연이 줄지만 전환점에서 오버슈트한다 —
        추세가 꺾이는 순간 필터는 아직 이전 기울기를 믿고 있다.
        DEMA/TEMA 가 겪는 것과 같은 문제다. 눈으로 '빨라 보인다'로
        고르면 안 되고, 반드시 IC 로 확인할 것. 지연을 줄인 대가로
        예측력이 떨어졌다면 그건 개선이 아니다.
 
    ■ 입력
        TimeEMA 와 같은 규약. source 를 주면 그 지표의 라인을,
        안 주면 이벤트의 src 필드를 읽는다.
 
    등록: self.kf = self.ind(KalmanTrend(q=1e-4, source=self.flow,
                                         line="ofi_norm"),
                             on="quote", name="ofi_kf")   ← flow 뒤에
    조회: .line("level") / .line("slope") / .line("lead")
    """
 
    def __init__(self, q: float = 1e-4, obs_var: Optional[float] = None,
                 source: Optional[Indicator] = None, line: str = "value",
                 src: Optional[str] = None, lead_s: float = 0.0,
                 var_halflife: float = 300.0, max_pair_dt: float = 1.0,
                 warmup_n: int = 50, nis_halflife: float = 300.0,
                 min_dt: float = 0.1):
        if source is None and src is None:
            raise ValueError("source 또는 src 중 하나는 있어야 한다")
        self.q = q
        self.fixed_obs_var = obs_var             # None 이면 온라인 추정
        self.source = source
        self.line_name = line
        self.src = src
        self.lead_s = lead_s
        self.var_halflife = var_halflife
        self.max_pair_dt = max_pair_dt
        self.warmup_n = warmup_n
        self.nis_halflife = nis_halflife
        self.min_dt = min_dt
 
        self._var_tau = var_halflife / math.log(2.0)
        self._nis_tau = nis_halflife / math.log(2.0)
        self._R: Optional[float] = obs_var       # 현재 잡음 분산 추정치
        self._R_floor = 0.0                      # 0 으로 죽지 않게 하는 하한
        self._seed: list = []                    # 시드용 표본 모음
        self._n_pairs = 0                        # 추정에 쓴 쌍 개수
        self._prev_z: Optional[float] = None     # Δz 계산용
        self._nis_avg: Optional[float] = None    # nis 이동평균
 
        self._level: Optional[float] = None
        self._slope = 0.0
        # 오차공분산 2x2. 대칭이라 세 개면 된다.
        self._p00 = 0.0
        self._p01 = 0.0
        self._p11 = 0.0
        self._prev_t: Optional[float] = None
 
    @property
    def warmup(self) -> Optional[int]:
        """온라인 추정이면 잡음 분산을 잡을 표본이 먼저 필요하다."""
        return None if self.fixed_obs_var is not None else self.warmup_n
 
    # ───────── 잡음 분산 온라인 추정 ─────────
    def _update_noise(self, z: float, dt: float) -> None:
        """(Δz)²/2 를 누적해 R 을 갱신한다.
 
        열린 루프다 — 필터 상태를 쓰지 않으므로 R 추정과 필터가 서로를
        밀어내며 발산할 일이 없다.
 
        ★ 시드를 표본 하나로 잡으면 안 된다 ★
        ofi_norm 은 호가창이 그대로면 정확히 0 이고, H0STASP0 은 호가가
        안 바뀌어도 주기적으로 오므로 Δz = 0 인 쌍이 흔하다. 첫 표본
        하나로 R 을 잡으면 그게 0 이 될 수 있고, 그러면 아래 상한
        min(sample, 25R) 이 이후 모든 표본을 0 으로 잘라 R 이 영원히
        0 에 갇힌다(흡수 상태). 필터가 시작조차 못 한다.
        그래서 warmup_n 개를 모아 평균으로 시드하고, 하한도 둔다."""
        if self.fixed_obs_var is not None:
            return
        if self._prev_z is None or dt > self.max_pair_dt:
            return                               # 간격이 벌어진 쌍은 버린다
 
        sample = (z - self._prev_z) ** 2 / 2.0
        self._n_pairs += 1
 
        if self._R is None:
            self._seed.append(sample)
            if len(self._seed) > self.warmup_n:
                self._seed.pop(0)                # 슬라이딩 — 버리지 않고 민다
            if len(self._seed) < self.warmup_n:
                return
            nonzero = [s for s in self._seed if s > 0]
            if len(nonzero) < max(4, self.warmup_n // 4):
                # 창이 거의 전부 조용하다. 여기서 시드를 잡으면 R 이
                # 터무니없이 작아지고 필터가 관측을 과신한다.
                # 창을 한 칸씩 밀면서 활동이 들어올 때까지 기다린다.
                return
            self._R = sum(self._seed) / len(self._seed)
            self._R_floor = self._R * 1e-6
            self._seed.clear()
            return
 
        # 급등락 한 건이 추정치를 통째로 끌고 가지 않게 상한을 둔다
        # (정규분포라면 25배 = 5시그마를 넘는 일은 드물다)
        sample = min(sample, 25.0 * self._R)
        k = 1.0 - math.exp(-dt / self._var_tau)
        self._R = max(sample * k + self._R * (1.0 - k), self._R_floor)
 
    def _ready_to_filter(self) -> bool:
        return self._R is not None and self._R > 0
 
    def update(self, ev):
        z = (self.source.line(self.line_name) if self.source is not None
             else getattr(ev, self.src, None))
        if z is None:
            return self.line("level")            # 입력이 아직 없다 — 값 유지
        z = float(z)
 
        t = _event_time(ev)
        dt = self.min_dt if self._prev_t is None else max(t - self._prev_t,
                                                          self.min_dt)
 
        self._update_noise(z, dt)
        self._prev_z = z
        self._prev_t = t
 
        if not self._ready_to_filter():
            # 아직 척도를 모른다. 관측을 그대로 흘리며 표본만 모은다.
            # 여기서 어설픈 값을 내면 워밍업 구간이 신호로 오인된다.
            self._values["obs_var"] = self._R
            self._values["nis"] = None
            self._values["level"] = None
            self._values["slope"] = None
            self._values["lead"] = None
            return None
 
        if self._level is None:
            # 필터 시작. 첫 관측이 곧 시드이고, P 를 크게 잡아
            # 초반에는 관측을 거의 그대로 받아들이게 한다.
            self._level, self._slope = z, 0.0
            self._p00 = self._R * 1e6
            self._p01 = 0.0
            self._p11 = self._R * 1e6
            self._emit(None)
            return self._level
 
        # ── 예측 ── x = F x,  P = F P Fᵀ + Q
        self._level += self._slope * dt
 
        p00 = self._p00 + 2 * dt * self._p01 + dt * dt * self._p11
        p01 = self._p01 + dt * self._p11
        p11 = self._p11
 
        # 등가속도(white-noise-acceleration) 모델의 표준 Q.
        # 프로세스 잡음이 Δt 에 비례해 커진다 — 오래 못 본 사이에
        # 상태가 더 많이 변했을 수 있다는 뜻이다.
        # Q 를 R 에 비례시키므로 종목 척도가 달라도 q 는 그대로 쓴다.
        qq = self.q * self._R
        p00 += qq * dt ** 3 / 3.0
        p01 += qq * dt ** 2 / 2.0
        p11 += qq * dt
 
        # ── 갱신 ── H = [1, 0] 이라 스칼라 관측
        s = p00 + self._R
        k0 = p00 / s
        k1 = p01 / s
 
        innovation = z - self._level
        self._level += k0 * innovation
        self._slope += k1 * innovation
 
        self._p00 = (1.0 - k0) * p00
        self._p01 = (1.0 - k0) * p01
        self._p11 = p11 - k1 * p01
 
        self._emit(innovation * innovation / s, dt)
        return self._level
 
    def _emit(self, nis, dt=None):
        """nis 는 순간값이 아니라 이동평균으로 낸다.
 
        순간 nis 는 자유도 1 의 카이제곱이라 중앙값이 0.45, 꼬리가 두껍다.
        화면에서 한 값만 보면 8.24 나 0.0008 이 예사로 뜨는데, 그걸로는
        필터가 잘 맞는지 판단할 수 없다. 평균이 1 근처인지가 판단 기준
        이므로 처음부터 평균을 낸다."""
        if nis is not None:
            if self._nis_avg is None:
                self._nis_avg = nis
            elif dt is not None:
                k = 1.0 - math.exp(-dt / self._nis_tau)
                self._nis_avg = nis * k + self._nis_avg * (1.0 - k)
 
        self._values["level"] = self._level
        self._values["slope"] = self._slope
        self._values["lead"] = (self._level + self._slope * self.lead_s
                                if self._level is not None else None)
        self._values["obs_var"] = self._R
        self._values["nis"] = self._nis_avg
 
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

    ■ 스냅샷의 한계
        H0STASP0 은 이벤트가 아니라 스냅샷이다. 두 스냅샷 사이에
        체결과 취소가 여러 번 있었어도 순변화 하나로만 보인다.
        여기서 나오는 값은 '주문 흐름'이 아니라 '스냅샷 간 순유출'이다.
        원리적 한계이므로 해석할 때 감안할 것.

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

        if self._prev_ask_p is None:
            # 첫 호가는 비교 대상이 없다. 사다리만 기록하고 넘어간다
            # (CrossOver 의 '첫 계산은 차이만 기록'과 같은 규칙).
            self._remember(quote, ask_p, bid_p, ask_q, bid_q)
            return None

        ask_flow, a_trunc = self._side_flow(
            self._prev_ask_p, ask_p, ask_q, self._prev_ask_ladder, is_ask=True)
        bid_flow, b_trunc = self._side_flow(
            self._prev_bid_p, bid_p, bid_q, self._prev_bid_ladder, is_ask=False)

        ofi = ask_flow - bid_flow
        denom = self._prev_top_qty
        ofi_norm = ofi / denom if denom > 0 else None

        self._remember(quote, ask_p, bid_p, ask_q, bid_q)

        self._values["ask_flow"] = ask_flow
        self._values["bid_flow"] = bid_flow
        self._values["ofi"] = ofi
        self._values["ofi_norm"] = ofi_norm
        self._values["truncated"] = 1.0 if (a_trunc or b_trunc) else 0.0
        return ofi

    def _remember(self, quote, ask_p, bid_p, ask_q, bid_q):
        self._prev_ask_p, self._prev_bid_p = ask_p, bid_p
        self._prev_ask_ladder = _ladder(quote, "ask")
        self._prev_bid_ladder = _ladder(quote, "bid")
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
