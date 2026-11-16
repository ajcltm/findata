"""
microstructure_kalman.py — 예전 KalmanTrend (legacy)

옛 위치: alpha/indicators/microstructure.py 의 KalmanTrend.
alpha/indicators/kalman.py 의 KalmanTrend 가 이 클래스를 대체한다.
대조군·과거 비교용으로 남긴다.
"""

from __future__ import annotations

import math
from typing import Optional

from alpha.indicators.indicators import Indicator
from alpha.indicators.microstructure import _event_time


# ★★★ 예전 버전 — 더 이상 실전에 등록하지 않는다 ★★★
# alpha.indicators.kalman.KalmanTrend 로 교체됐다(q 를 fixed 비율로 받고
# R 만 온라인 추정하던 버전 — 새 버전은 q̂·R̂ 를 둘 다 두 lag·두 반감기로
# 온라인 추정하고, 추가 진단 라인(surprise/r_surprise 등)이 있다. 중복
# 스냅샷 판정도 이 클래스처럼 자체 처리하지 않고, 상류 BestQuoteFlow 의
# is_dup 플래그를 읽는 방식으로 바뀌었다). MicrostructureWatcher.setup()
# 은 이제 새 버전을 쓴다(strategy/microstructure_watcher.py 참고).
# 지우지 않고 대조군·과거 비교용으로 남겨둔다 — 삭제하지 말 것.
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
        sample = min(sample, 1000.0 * self._R)
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
