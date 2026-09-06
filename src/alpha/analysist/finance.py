"""FinanceMixin — Tdata에 섞여 들어가는 주식 금융 지표 계산 모음.

■ 이 파일이 하는 일
    tdata.py의 Tdata는 "시간축을 가진 데이터를 담고 겹쳐 다루는 범용
    그릇"이고, 여기(finance.py)는 그 위에 얹는 "주식 금융 도메인" 전용
    계산들이다. tdata.py는 이 파일을 몰라도 되고(순수 그릇으로 남는다),
    Tdata는 FinanceMixin을 상속해서 이 메서드들을 전부 물려받는다:

        class Tdata(FinanceMixin):
            ...

    그래서 쓰는 쪽에서는 파일이 나뉘어 있다는 걸 몰라도 된다 —
    td.relative()/td.sma(window=20) 처럼 그냥 Tdata 메서드로 보인다.

■ 분류 원칙 — "이 계산이 어떤 윈도우 모양을 요구하는가"
    - calc                  : 유일하게 새 leaf를 안 만든다 — base.df에
                               컬럼을 하나 더할 뿐이다(사칙연산처럼 "그
                               자체로 새로운 관측 대상"이 아니라 "기존
                               컬럼들의 단순 조합"이라 별도 leaf로 분리할
                               이유가 없다).
    - relative/pct_change   : 윈도우 없음(고정 기준 또는 한 칸 지연 비교)
    - cumulative_rate/cagr/volatility/sharpe/exceed/sma/ema/zscore
                             : 슬라이딩(고정폭) 윈도우 — 전부 자기만의
                               window(그리고 필요하면 freq/threshold)를
                               따로 받는 "원자" 함수다. 한 함수가 여러
                               개를 묶어서 계산해주지 않는다 — SMA(5)와
                               SMA(20)처럼 같은 지표를 다른 파라미터로
                               나란히 비교하고 싶은 경우가 흔해서, 처음부터
                               쪼개 두는 게 나중에 덜 불편하다(이 판단의
                               배경은 대화 기록 참고).
    - drawdown/benchmark    : 그 자체로 여러 값을 "한 계산의 부산물"로
                               내놓는 것들(drawdown 계산 한 번이면 낙폭·
                               지속기간·누적최고가가 다 같이 나온다) — 이
                               런 건 묶어두고, metrics=로 필요한 컬럼만
                               고르게 한다.
    - agg/summary           : Tdata가 아니라 스칼라(요약값)를 내놓는
                               "체인이 끝나는" 함수들. agg()가 기본 도구
                               (스칼라 하나 계산하는 함수를 사용자가 직접
                               주는 범용 버전)이고, summary()는 자주 쓰는
                               계산식들을 미리 묶어둔 것 — agg()를 여러
                               번 부른 결과를 컬럼으로 모아 표 하나로
                               돌려준다.

■ 이름 짓는 규칙 — "원본_지표파라미터@컬럼"
    파생 leaf 이름은 반드시 base leaf 이름을 접두어로 포함한다
    (예: base.name="bar" → "bar_sma20@close"). 이래야 서로 다른 원본
    테이블에서 파생된 같은 이름의 지표끼리 이름이 겹치지 않는다.
    (다른 "종목" 사이의 충돌은 이것과는 별개로, base.keys를 그대로
    물려받는 것으로 막는다 — 아래 _new_leaf 참고.)

■ 그룹(종목) 처리
    base.keys가 있으면(예: ("symbol",)) 전부 그 키로 묶어서 계산한다 —
    한 Tdata 안에 여러 종목이 섞여 있어도 종목 경계를 넘어 데이터가
    새지 않는다(예: A 종목의 누적수익률이 B 종목으로 이어붙는 사고가
    안 난다). keys가 없으면 그냥 전체를 하나의 시계열로 본다.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

import numpy as np
import pandas as pd

from .tdata import Leaf

# ---------------------------------------------------------------------
# 순수 계산 헬퍼(그룹 하나짜리 pd.Series를 받아 값 하나를 돌려준다) —
# drawdown()/summary() 양쪽이 "낙폭/회복 지속기간" 개념을 공유하므로
# 여기 한 곳에만 적어둔다.
# ---------------------------------------------------------------------
def _mdd(prices: pd.Series) -> float:
    """가장 깊었던 낙폭(최소값, 음수) — 그룹 하나짜리 가격 시계열 기준."""
    return (prices / prices.cummax() - 1).min()


def _peak_group(prices: pd.Series) -> pd.Series:
    """"몇 번째 고점 이후 구간인가"를 나타내는 정수 시리즈.

    누적최고가(cummax)를 새로 경신한 시점(is_peak=True)마다 번호가 하나씩
    올라간다 — 그 번호가 같은 행들은 전부 "같은 고점에서 아직 회복 못한
    같은 구간"이다. cumcount()와 짝지으면 "그 구간이 시작된 지 몇 번째
    행인가"(회복 지연 기간)를 셀 수 있다."""
    is_peak = prices == prices.cummax()
    return is_peak.cumsum()


def _max_dd_duration(prices: pd.Series) -> int:
    """가장 길었던 고점 회복 지연 기간(행 개수 기준) — 그룹 하나짜리."""
    grp = _peak_group(prices)
    # Series.groupby(그 자신)으로 "같은 구간 번호"별로 묶은 뒤 cumcount() —
    # 그 구간이 시작되고 몇 번째 행인지를 0부터 센다. 최댓값이 곧
    # "가장 오래 걸린 회복"이다.
    return int(grp.groupby(grp).cumcount().max())


class FinanceMixin:
    """Tdata에 섞여 들어가는 금융 지표 메서드들. 단독으로 쓰지 않는다 —
    self._base/self.add()/self.leaves 등 Tdata의 내부 구조에 기대어
    동작하므로, Tdata를 상속받는 클래스에서만 의미가 있다."""

    # -- 내부 공용 도우미 --------------------------------------------------
    def _derived_name(self, metric: str, column: str, param: Optional[str] = None) -> str:
        """"원본_지표파라미터@컬럼" 형태로 파생 leaf 이름을 짓는다.
        예: base.name="bar", metric="sma", column="close", param="20"
            -> "bar_sma20@close" """
        suffix = f"{metric}{param}" if param is not None else metric
        return f"{self._base.name}_{suffix}@{column}"

    def _new_leaf(self, name: str, columns: dict) -> Leaf:
        """계산된 값 컬럼(들)과 base의 키 컬럼(들)을 합쳐 새 Leaf를 만든다.
        키를 그대로 물려받는 게 핵심이다 — 그래야 여러 종목이 섞인
        Tdata에서 계산해도 종목별로 안전하게 구분된 채로 남고, 나중에
        다른 Tdata와 합쳐도(add()) 종목 단위로 정확히 병합된다."""
        df = pd.DataFrame(columns, index=self._base.df.index)
        for k in self._base.keys:
            df[k] = self._base.df[k].values
        return Leaf(name, df, keys=self._base.keys)

    def _append(self, leaf: Leaf) -> "Tdata":
        """이 파일의 메서드들이 만든 파생 leaf를 others에 붙인다.

        add()가 아니라 이 메서드를 쓰는 이유: add()는 "독립적으로 얻어진
        두 데이터가 우연히 같은 (t,*keys)에서 서로 다른 값을 내면 그건
        진짜 데이터 모순이니 바로 알려야 한다"는 계약을 지킨다(모듈 맨
        위 docstring의 "입력은 계약이다" 참고). 그런데 quote/tick처럼
        같은 초에 여러 행이 있을 수 있는 데이터(=애초에 (t,*keys)가
        유일하지 않은 데이터)에서 sma() 같은 걸 계산하면, 같은 시각의
        서로 다른 행이 서로 다른 sma 값을 갖는 게 정상이다(각자 다른
        위치의 rolling 윈도우 결과라서) — 이건 계산 오류나 데이터 모순이
        아니라 원본 데이터의 정상적인 특성이 그대로 반영된 것뿐인데,
        add()의 충돌검사는 이걸 구분 못 하고 에러를 낸다. 이 파일이
        만드는 leaf는 전부 base에서 결정론적으로 계산된 것이라(독립
        소스가 섞여서 진짜로 모순될 위험이 없다) 그 검사를 건너뛴다."""
        return self._spawn(self._base, self._others + (leaf,))

    def _replace_base(self, leaf: Leaf) -> "Tdata":
        """replace=True일 때 쓴다 — leaf를 others에 붙이는 대신, 이
        leaf 자체를 새 base로 통째로 바꿔치기한다(calc()처럼 기존
        base.df에 컬럼만 더하는 게 아니다 — 그 결과 leaf의 df가 곧
        새 base.df가 된다).

        그래서 기존 base가 갖고 있던 다른 컬럼(예: bar의 open/high/low/
        volume)은 여기서 사라진다 — relative()/sma() 등이 만드는 leaf는
        자기 결과 컬럼(들) + 키 컬럼만 담고 있어서다(_new_leaf 참고).
        그 컬럼들이 계속 필요하면 replace=True를 쓰기 전에 따로 남겨
        두거나(예: calc()로 미리 옮겨두기), replace=False(기본)로 얻은
        결과에서 .df로 직접 꺼내 써야 한다."""
        return self._spawn(leaf, self._others)

    def _rate_series(self, column: str) -> pd.Series:
        """base.keys 기준으로 그룹지어 계산한 기간수익률(pct_change).
        relative/pct_change 뿐 아니라 cumulative_rate/cagr/volatility/
        sharpe/exceed가 전부 이 "수익률" 하나를 공유해서 쓴다."""
        df = self._base.df
        if self._base.keys:
            return df.groupby(list(self._base.keys), observed=True, sort=False)[column].pct_change()
        return df[column].pct_change()

    def _group_transform(self, series: pd.Series, fn: Callable[[pd.Series], pd.Series]) -> pd.Series:
        """series를 base.keys로 그룹지어 fn(그 그룹의 series)을 적용한다.
        fn은 "그룹 하나짜리 Series -> 같은 길이의 Series"인 함수라면
        무엇이든 된다(rolling().mean(), ewm().mean(), cummax() 등).
        keys가 없으면 그냥 전체 series에 한 번만 적용한다."""
        if not self._base.keys:
            return fn(series)
        tmp = pd.DataFrame({"_v": series})
        for k in self._base.keys:
            tmp[k] = self._base.df[k].values
        return tmp.groupby(list(self._base.keys), observed=True, sort=False)["_v"].transform(fn)

    @staticmethod
    def _check_metrics(metrics: Sequence[str], allowed: Sequence[str]) -> None:
        bad = [m for m in metrics if m not in allowed]
        if bad:
            raise ValueError(f"모르는 metrics: {bad} (알려진 것: {', '.join(allowed)})")

    # -- 윈도우 없음 --------------------------------------------------------
    def relative(self, column: str = "close", replace: bool = False) -> "Tdata":
        """그룹별 "첫 값" 대비 비율로 정규화한다 — 여러 종목의 등락을
        같은 출발선(1.0)에서 겹쳐 비교하고 싶을 때 쓴다.
        replace=True면 새 leaf를 만들어 붙이는 대신, 이 결과(relative
        컬럼 + 키)가 통째로 새 base가 된다 — 기존 base의 close 등 다른
        컬럼은 사라진다(아래 모든 지표 메서드가 공통으로 갖는 옵션이다.
        자세한 건 _replace_base 참고)."""
        df = self._base.df
        if self._base.keys:
            first = df.groupby(list(self._base.keys), observed=True, sort=False)[column].transform("first")
        else:
            first = df[column].iloc[0]
        relative = df[column] / first
        leaf = self._new_leaf(self._derived_name("relative", column), {"relative": relative})
        return self._replace_base(leaf) if replace else self._append(leaf)

    def pct_change(self, column: str = "close", periods: int = 1,
                  threshold: float = 0, replace: bool = False) -> "Tdata":
        """기간수익률(rate)과, 그 값이 threshold를 넘겼는지(exceeds)를
        같이 낸다. exceeds는 임계값 하나로 "이겼는지/기준을 넘겼는지"를
        표시하는 범용 표시자다 — threshold=0(기본)이면 "수익이 났는가"가
        된다. replace=True면 이 결과(rate/exceeds + 키)가 통째로 새
        base가 된다(기존 base의 다른 컬럼은 사라진다)."""
        df = self._base.df
        if self._base.keys:
            rate = df.groupby(list(self._base.keys), observed=True, sort=False)[column].pct_change(periods=periods)
        else:
            rate = df[column].pct_change(periods=periods)
        exceeds = rate > threshold
        leaf = self._new_leaf(self._derived_name("pct_change", column),
                              {"rate": rate, "exceeds": exceeds})
        return self._replace_base(leaf) if replace else self._append(leaf)

    # -- base 자체를 늘리는 것(새 leaf가 아니라 base.df에 컬럼을 더한다) -----------
    def calc(self, name: str, left, op: str, right) -> "Tdata":
        """base의 컬럼(또는 상수)끼리 사칙연산을 해서 base.df에 컬럼을
        하나 늘린다. 다른 메서드들과 달리 새 leaf를 만들어 others에
        붙이는 게 아니라, 지금 base 그 자체에 컬럼이 하나 추가된 새
        Tdata를 반환한다(체인은 그대로 이어간다).

            td.calc("spread", "high", "-", "low")     # spread = high - low
            td.calc("mid", "spread", "/", 2)          # 방금 만든 spread를 또 참조 가능
            td.calc("double_close", "close", "*", 2)  # 상수와도 계산된다

        left/right : 문자열이면 base.df의 그 컬럼을, 숫자면 그 값
                    그대로(상수)를 쓴다.
        op         : "+"/"-"/"*"/"/" 중 하나.
        name       : 새로 생길 컬럼 이름 — 자동으로 짓지 않는다. 사칙
                    연산은 sma처럼 "이게 무슨 뜻의 계산인지"가 미리
                    정해져 있지 않아서, 이름을 자동으로 지으면 오히려
                    헷갈리기 쉽다 — 그래서 반드시 사용자가 뜻을 담아
                    직접 짓게 했다. 이미 있는 컬럼 이름을 주면 그
                    컬럼을 덮어쓴다(평범한 pandas의 df[name] = ... 과
                    같은 동작이다)."""
        ops = {
            "+": lambda a, b: a + b,
            "-": lambda a, b: a - b,
            "*": lambda a, b: a * b,
            "/": lambda a, b: a / b,
        }
        if op not in ops:
            raise ValueError(f"모르는 연산자: {op!r} (알려진 것: {', '.join(ops)})")
        df = self._base.df
        # 문자열이면 그 이름의 컬럼을, 아니면(숫자 등) 상수 그 자체를 쓴다.
        lval = df[left] if isinstance(left, str) else left
        rval = df[right] if isinstance(right, str) else right
        new_df = df.copy(deep=False)
        new_df[name] = ops[op](lval, rval)
        return self._spawn(self._base.with_df(new_df), self._others)

    # -- 슬라이딩 윈도우(각자 독립 파라미터) -----------------------------------
    def cumulative_rate(self, column: str = "close", window: int = 20,
                       replace: bool = False) -> "Tdata":
        """최근 window 기간 동안의 누적 성장률(예: 1.05 = +5%).

        (1+수익률)의 곱을 rolling().apply(np.prod)로 직접 구하면 창
        하나하나를 파이썬 레벨에서 돌기 때문에 느리다 — 대신 로그를 취해
        rolling 합을 구한 뒤 지수를 취하는(logsumexp 트릭) 방식으로
        완전히 벡터화한다: prod(1+r) = exp(sum(log(1+r)))."""
        rate = self._rate_series(column)
        log_growth = self._group_transform(np.log1p(rate), lambda s: s.rolling(window).sum())
        cumulative_rate = np.exp(log_growth)
        leaf = self._new_leaf(self._derived_name("cumulative_rate", column, str(window)),
                              {"cumulative_rate": cumulative_rate})
        return self._replace_base(leaf) if replace else self._append(leaf)

    def cagr(self, column: str = "close", window: int = 20, freq: int = 252,
            replace: bool = False) -> "Tdata":
        """최근 window 기간 성장률을 연율화한 것(연 환산 복리 수익률).

        freq는 "1년에 이 데이터가 몇 개 있는가"다 — 일봉이면 252(거래일
        기준 관행값), 분봉이면 그만큼 다른 값을 직접 줘야 한다(이 파일은
        데이터가 어떤 주기인지 스스로 판단하지 않는다)."""
        rate = self._rate_series(column)
        log_growth = self._group_transform(np.log1p(rate), lambda s: s.rolling(window).sum())
        growth = np.exp(log_growth)
        cagr = growth ** (freq / window) - 1
        leaf = self._new_leaf(self._derived_name("cagr", column, str(window)), {"cagr": cagr})
        return self._replace_base(leaf) if replace else self._append(leaf)

    def volatility(self, column: str = "close", window: int = 20, freq: int = 252,
                  replace: bool = False) -> "Tdata":
        """최근 window 기간 수익률의 표준편차를 연율화한 것.
        freq는 cagr()과 같은 뜻(1년 안의 데이터 개수)이다."""
        rate = self._rate_series(column)
        std = self._group_transform(rate, lambda s: s.rolling(window).std())
        volatility = std * np.sqrt(freq)
        leaf = self._new_leaf(self._derived_name("volatility", column, str(window)),
                              {"volatility": volatility})
        return self._replace_base(leaf) if replace else self._append(leaf)

    def sharpe(self, column: str = "close", window: int = 20, freq: int = 252,
              replace: bool = False) -> "Tdata":
        """최근 window 기간의 cagr/volatility 비율(무위험수익률은 0으로
        가정한 단순화된 샤프 지수). cagr()/volatility()와 같은 중간값을
        내부에서 다시 계산한다(공개 API는 결과 하나만 내놓기 위해 —
        cagr()/volatility()를 따로 또 부르고 싶으면 그건 별도로 부르면
        된다)."""
        rate = self._rate_series(column)
        log_growth = self._group_transform(np.log1p(rate), lambda s: s.rolling(window).sum())
        cagr = np.exp(log_growth) ** (freq / window) - 1
        std = self._group_transform(rate, lambda s: s.rolling(window).std())
        volatility = std * np.sqrt(freq)
        sharpe = cagr / volatility
        leaf = self._new_leaf(self._derived_name("sharpe", column, str(window)), {"sharpe": sharpe})
        return self._replace_base(leaf) if replace else self._append(leaf)

    def exceed(self, column: str = "close", window: int = 20, threshold: float = 0,
              metrics: Sequence[str] = ("count", "rate"), replace: bool = False) -> "Tdata":
        """최근 window 기간 동안 수익률이 threshold를 몇 번(count)/몇
        비율(rate)로 넘겼는지 — pct_change()의 exceeds를 구간으로 집계한
        버전이다. threshold=0(기본)이면 "이긴 기간의 비율"(승률)이 된다."""
        self._check_metrics(metrics, ("count", "rate"))
        rate = self._rate_series(column)
        flag = (rate > threshold).astype(float)   # rolling sum/mean이 되도록 0.0/1.0으로
        out = {}
        if "count" in metrics:
            out["exceed_count"] = self._group_transform(flag, lambda s: s.rolling(window).sum())
        if "rate" in metrics:
            out["exceed_rate"] = self._group_transform(flag, lambda s: s.rolling(window).mean())
        leaf = self._new_leaf(self._derived_name("exceed", column, str(window)), out)
        return self._replace_base(leaf) if replace else self._append(leaf)

    def sma(self, column: str = "close", window: int = 20, replace: bool = False) -> "Tdata":
        """단순이동평균(산술 이동평균)."""
        price = self._base.df[column]
        sma = self._group_transform(price, lambda s: s.rolling(window).mean())
        leaf = self._new_leaf(self._derived_name("sma", column, str(window)), {"sma": sma})
        return self._replace_base(leaf) if replace else self._append(leaf)

    def ema(self, column: str = "close", span: int = 20, replace: bool = False) -> "Tdata":
        """지수이동평균 — 최근 값에 더 큰 가중치를 주는 이동평균.
        span은 이동평균 창 폭과 비슷한 감각의 파라미터다(pandas ewm 표준
        인자 이름을 그대로 따른다)."""
        price = self._base.df[column]
        ema = self._group_transform(price, lambda s: s.ewm(span=span).mean())
        leaf = self._new_leaf(self._derived_name("ema", column, str(span)), {"ema": ema})
        return self._replace_base(leaf) if replace else self._append(leaf)

    def zscore(self, column: str = "close", window: int = 20, replace: bool = False) -> "Tdata":
        """최근 window 기간 평균/표준편차 기준으로 지금 값이 몇 표준편차
        떨어져 있는지. 평균회귀 전략 분석에 흔히 쓰인다."""
        price = self._base.df[column]
        mean = self._group_transform(price, lambda s: s.rolling(window).mean())
        std = self._group_transform(price, lambda s: s.rolling(window).std())
        zscore = (price - mean) / std
        leaf = self._new_leaf(self._derived_name("zscore", column, str(window)), {"zscore": zscore})
        return self._replace_base(leaf) if replace else self._append(leaf)

    # -- 한 계산의 여러 부산물을 묶어서 -----------------------------------------
    def drawdown(self, column: str = "close",
                metrics: Sequence[str] = ("drawdown", "dd_periods", "cumulative_max"),
                replace: bool = False) -> "Tdata":
        """누적최고가(cumulative_max) 대비 현재 낙폭(drawdown, 음수)과,
        마지막 고점 이후 몇 기간째 회복을 못 했는지(dd_periods)를 낸다.

        dd_periods는 원본(참고했던 옛 코드)의 "dd_days"(달력 일수)를
        빈도 무관하게 일반화한 것이다 — 분봉/틱 데이터에서는 "며칠"보다
        "몇 번째 데이터인지"가 더 의미 있다. 실제 경과 시간이 필요하면
        metrics에 "dd_time"을 추가한다(Timedelta로 나온다)."""
        allowed = ("drawdown", "dd_periods", "dd_time", "cumulative_max")
        self._check_metrics(metrics, allowed)
        price = self._base.df[column]
        cummax = self._group_transform(price, lambda s: s.cummax())

        out = {}
        if "cumulative_max" in metrics:
            out["cumulative_max"] = cummax
        if "drawdown" in metrics:
            out["drawdown"] = price / cummax - 1
        if "dd_periods" in metrics or "dd_time" in metrics:
            grp = self._group_transform(price, _peak_group)
            # 종목(keys)과 grp(몇 번째 고점 구간인지)를 같이 묶어야
            # "그 종목의 그 구간이 시작되고 몇 번째인지"를 정확히 센다.
            tmp = pd.DataFrame({"_grp": grp})
            for k in self._base.keys:
                tmp[k] = self._base.df[k].values
            group_cols = list(self._base.keys) + ["_grp"]
            periods = tmp.groupby(group_cols, observed=True, sort=False).cumcount()
            periods.index = price.index
            if "dd_periods" in metrics:
                out["dd_periods"] = periods
            if "dd_time" in metrics:
                # 그 구간이 시작된 시각(=고점 시각)을 종목·구간별로 구해서,
                # 지금 시각과의 차이를 그대로 Timedelta로 낸다.
                start_time = tmp.assign(_t=price.index).groupby(
                    group_cols, observed=True, sort=False)["_t"].transform("min")
                start_time.index = price.index
                out["dd_time"] = pd.Series(price.index, index=price.index) - start_time

        leaf = self._new_leaf(self._derived_name("drawdown", column), out)
        return self._replace_base(leaf) if replace else self._append(leaf)

    def benchmark(self, other: "Tdata", column: str = "close",
                 other_column: Optional[str] = None, window: int = 60,
                 metrics: Sequence[str] = ("beta", "alpha", "correlation", "r_squared"),
                 replace: bool = False) -> "Tdata":
        """다른 Tdata(예: 지수, 다른 종목)를 기준(benchmark)으로 놓고,
        최근 window 기간 동안의 회귀 계수를 계산한다 — 나중에 KOSPI나
        S&P 데이터를 확보하면 그대로 beta 분석에 쓸 수 있고, 개별 종목을
        other로 넣으면 일반적인 두 시계열 간 회귀 분석이 된다.

        other는 단일 시계열(키 없음, 또는 종목 하나)이라고 가정한다 —
        여러 종목이 섞인 Tdata를 benchmark로 쓰는 건 지원하지 않는다.
        self는 종목이 여러 개(keys 있음)여도 된다 — 각 종목마다 같은
        other 하나를 기준으로 따로 회귀한다."""
        allowed = ("beta", "alpha", "correlation", "r_squared")
        self._check_metrics(metrics, allowed)
        other_column = other_column or column

        self_rate = self._rate_series(column)
        other_rate = other._base.df[other_column].pct_change()

        tmp = pd.DataFrame({"_self": self_rate})
        for k in self._base.keys:
            tmp[k] = self._base.df[k].values
        # other_rate를 시각(인덱스) 기준으로 이어 붙인다 — self 쪽에
        # 있는 시각에 other 쪽 값이 없으면(시간축이 안 맞으면) NaN이
        # 되고, 그 지점의 rolling 계산은 자동으로 NaN 처리된다.
        tmp["_bench"] = other_rate.reindex(tmp.index)

        def _calc(g: pd.DataFrame) -> pd.DataFrame:
            x, y = g["_bench"], g["_self"]
            cov = y.rolling(window).cov(x)
            var = x.rolling(window).var()
            beta = cov / var
            corr = y.rolling(window).corr(x)
            result = pd.DataFrame(index=g.index)
            if "beta" in metrics:
                result["beta"] = beta
            if "alpha" in metrics:
                result["alpha"] = y.rolling(window).mean() - beta * x.rolling(window).mean()
            if "correlation" in metrics:
                result["correlation"] = corr
            if "r_squared" in metrics:
                result["r_squared"] = corr ** 2
            return result

        if self._base.keys:
            result = tmp.groupby(list(self._base.keys), observed=True, sort=False,
                                 group_keys=False).apply(_calc)
        else:
            result = _calc(tmp)

        name = f"{self._base.name}_benchmark{window}@{column}~{other._base.name}"
        leaf = self._new_leaf(name, {c: result[c] for c in result.columns})
        return self._replace_base(leaf) if replace else self._append(leaf)

    # -- 체인이 끝나는 것들(스칼라/DataFrame 반환) -------------------------------
    def agg(self, fn: Callable[[pd.Series], object], column: str = "close"):
        """base의 column을 그룹별로(keys가 있으면) fn에 넘겨 스칼라 하나로
        줄인다. fn은 "그 그룹의 시간순 Series -> 스칼라"인 아무 함수나
        된다. keys가 있으면 결과는 종목별 값을 담은 pd.Series, 없으면
        그냥 스칼라 하나다.

            td.agg(lambda s: (1 + s.pct_change()).prod() - 1, column="close")
        """
        df = self._base.df
        if self._base.keys:
            return df.groupby(list(self._base.keys), observed=True, sort=False)[column].apply(fn)
        return fn(df[column])

    def summary(self, column: str = "close", freq: int = 252, threshold: float = 0,
               specs: Optional[dict] = None) -> pd.DataFrame:
        """자주 쓰는 요약 지표들을 한 표로. specs를 안 주면 기본값(누적
        수익률/연율화수익률/변동성/샤프지수/최대낙폭/최대회복지연/승리
        횟수/승률)을 전부 계산한다. specs={"내이름": fn, ...}로 원하는
        것만 골라 완전히 새로 정의할 수도 있다(agg()와 같은 규칙의 fn).

        결과는 keys가 있으면 종목별 한 행씩, 없으면 한 행짜리 DataFrame."""
        if specs is None:
            def _rate(s):
                return s.pct_change().dropna()

            def _cagr(s):
                r = _rate(s)
                return (1 + r).prod() ** (freq / len(r)) - 1 if len(r) else np.nan

            def _annual_vol(s):
                r = _rate(s)
                return r.std() * np.sqrt(freq)

            specs = {
                "cumulative_return": lambda s: (1 + _rate(s)).prod() - 1,
                "cagr": _cagr,
                "period_vol": lambda s: _rate(s).std(),
                "annual_vol": _annual_vol,
                "sharpe_ratio": lambda s: _cagr(s) / _annual_vol(s),
                "mdd": _mdd,
                "max_dd_duration": _max_dd_duration,
                "win_count": lambda s: int((_rate(s) > threshold).sum()),
                "win_rate": lambda s: (_rate(s) > threshold).mean(),
            }

        cols = {name: self.agg(fn, column=column) for name, fn in specs.items()}
        if self._base.keys:
            return pd.DataFrame(cols)
        return pd.DataFrame([cols])
