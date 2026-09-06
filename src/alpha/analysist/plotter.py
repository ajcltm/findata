"""Tdata 플로터 (seaborn).

축 배분 규칙
-----------
기본: staged 결과의 name 순서대로
      0번 -> 1단 왼쪽축, 1번 -> 1단 오른쪽축,
      2번 -> 2단 왼쪽축, 3번 -> 2단 오른쪽축, ...
지정: layout=[(왼쪽 name 리스트, 오른쪽 name 리스트), ...]
      한 축에 여러 name 을 넣으면 같은 y 규격으로 함께 그린다.
      선택자는 "name"/"name.column"/"name.column[키값]"(Tdata.series()가
      보여주는 문자열) 세 가지를 쓸 수 있다 — 마지막 형태는 그 leaf의
      키(종목 등) 중 딱 하나만 그리고 싶을 때 쓴다.

그리는 형태(kind)
-----------------
기본은 전부 선(line)이다. 체결가처럼 점으로 찍는 게 자연스러운 것도
있고(mark), 거래량/카운트류처럼 막대로 보는 게 자연스러운 것도 있고
(bar), OHLC(open/high/low/close)를 캔들차트로 보고 싶을 수도 있어서
(candle), kind= 로 선택자별로 다르게 그릴 수 있다:
    kind="mark"                         전부 mark로
    kind={"bar_exceed10@close": "bar"}  이 name만 bar, 나머지는 기본(line)
    kind={"bar": "candle"}              "bar" leaf 전체를 캔들차트로
선택자는 layout과 같은 문법이다("name" 또는 "name.column"). candle은
반드시 "name"(leaf 전체) 선택자여야 한다 — open/high/low/close 네
컬럼이 한 leaf 안에 다 있어야 하나의 캔들이 만들어지기 때문이다.

■ 이 파일이 하는 일 (초보자를 위한 배경 설명)
    Tdata 안에 여러 Leaf(예: 봉 데이터, 지표 데이터)가 들어 있을 때,
    이걸 화면에 "몇 단으로 나눠서, 각 단은 왼쪽/오른쪽 축으로 나눠서"
    그려주는 게 이 파일의 역할이다. 보통은 Tdata.plot()을 통해 간접적
    으로 호출되고(tdata.py의 Tdata.plot() 메서드 참고), 이 파일의
    함수를 직접 부를 일은 거의 없다.

    matplotlib은 "그림 하나(fig)" 안에 "축(ax, axes) 여러 개"를 격자
    모양으로 배치할 수 있게 해주는 그래프 라이브러리이고, seaborn은
    그 위에서 좀 더 손쉽게 예쁜 선 그래프 등을 그려주는 라이브러리다.
    이 파일은 두 라이브러리를 함께 써서, Tdata 안의 여러 시계열을
    "몇 단짜리, 왼쪽/오른쪽 두 축짜리" 그래프로 자동 배치해준다.
"""

from __future__ import annotations

from typing import Sequence, Union

import matplotlib.pyplot as plt   # 그림(figure)/축(axes)을 만들고 창을 띄우는 라이브러리
import numpy as np
import pandas as pd
import seaborn as sns             # matplotlib 위에서 더 예쁜 선 그래프 등을 그려주는 라이브러리

from .tdata import TIME, Leaf, Name, Tdata, to_long

# Layout 은 "레이아웃(그림 배치)을 어떻게 지정하는지"에 대한 타입 별명이다.
# 실제 값은 이런 모양이다:
#     [ (["price"], ["volume"]),        # 1단: 왼쪽에 price, 오른쪽에 volume
#       (["indicator@MACD"], []) ]      # 2단: 왼쪽에 MACD, 오른쪽은 비움
# 즉, "(왼쪽에 그릴 이름들, 오른쪽에 그릴 이름들)" 튜플을 단(행)마다
# 하나씩 리스트로 나열한 것이 Layout이다.
Layout = Sequence[tuple[Sequence[str], Sequence[str]]]

# Kind 는 "무엇을 어떻게 그릴지"에 대한 타입 별명이다. 문자열 하나를
# 주면 전부 그 형태로, {"선택자": "형태", ...} 딕셔너리를 주면 선택자별로
# 다르게(딕셔너리에 없는 선택자는 "line") 그린다. 선택자 문법은 layout과
# 동일하다("name" 또는 "name.column").
Kind = Union[str, dict]

# 지금 지원하는 형태. 나중에 늘리려면(예: "area"/"step") 여기에 이름을
# 추가하고 _draw_series()에 그 형태를 그리는 분기 하나만 더하면 된다.
_KINDS = ("line", "mark", "bar", "candle")

# seaborn이 제공하는 "배경 스타일" 이름들. sns.set_theme()/sns.axes_style()
# 이 받는 값 그대로다 — 회색 격자 배경(darkgrid, seaborn 기본값)부터
# 흰 배경에 테두리만 있는 것(ticks)까지 있다.
_THEMES = ("darkgrid", "whitegrid", "dark", "white", "ticks")


def default_layout(names: Sequence[str]) -> list[tuple[list[str], list[str]]]:
    """이름 순서대로 (좌, 우) 슬롯을 채우며 단을 늘린다.

    layout을 따로 안 주면 이 함수가 자동으로 배치를 정한다: 이름이
    ["price", "volume", "macd"] 이면
        1단: (["price"], ["volume"])
        2단: (["macd"], [])
    처럼 두 개씩 짝지어 왼쪽/오른쪽에 채우고, 홀수로 하나 남으면 그
    한 개만 왼쪽에 둔 단을 마지막에 추가한다."""
    rows: list[tuple[list[str], list[str]]] = []
    left: list[str] = []
    right: list[str] = []
    for i, n in enumerate(names):
        # i % 2 : i를 2로 나눈 나머지. 0,2,4...번째는 왼쪽(0), 1,3,5...
        # 번째는 오른쪽(1)에 배정한다는 뜻이다("짝수 인덱스는 왼쪽").
        # "(left if i % 2 == 0 else right).append(n)" 은 "조건에 따라
        # left 또는 right 리스트 중 하나를 고른 뒤, 거기에 n을 추가한다"는
        # 뜻 — 파이썬에서는 이렇게 조건식으로 "어느 리스트를 쓸지" 자체를
        # 고를 수 있다.
        (left if i % 2 == 0 else right).append(n)
        if i % 2 == 1:
            # 오른쪽까지 막 채웠으면(왼쪽+오른쪽 한 쌍이 완성됐으면) 한
            # 단(row)으로 확정해서 rows에 담고, left/right를 다시 빈
            # 리스트로 초기화해서 다음 단을 준비한다.
            rows.append((left, right))
            left, right = [], []
    if left or right:
        # 이름 개수가 홀수여서 마지막에 왼쪽만(또는 오른쪽만) 채워진 채
        # 남아있으면, 그것도 마지막 단으로 추가해준다.
        rows.append((left, right))
    return rows


def _color_map(longs: dict[str, "object"], palette: str) -> dict[str, tuple]:
    """모든 시리즈(선 하나하나)에 고정된 색을 배정한다.

    왜 미리 정해두는가: 같은 시리즈("price.close[005930]" 등)가 왼쪽
    축과 오른쪽 축 양쪽에 걸쳐 나올 수도 있는데, 그때마다 색이 달라지면
    헷갈린다. 그리기 시작하기 전에 시리즈마다 색을 하나씩 고정해두고,
    실제로 그릴 때는 이 매핑을 그대로 갖다 쓴다."""
    series = []
    for lg in longs.values():
        # Series.unique() : 그 컬럼에 나오는 값들 중 중복을 없앤 목록.
        for s in lg["series"].unique():
            if s not in series:
                series.append(s)
    # sns.color_palette(palette, n) : palette라는 이름의 색상 팔레트
    # (예: "tab10")에서 n개의 색을 뽑아준다. max(..., 1)은 시리즈가
    # 하나도 없어도(len(series)==0) 최소 1개는 요청하도록 방어한 것.
    colors = sns.color_palette(palette, max(len(series), 1))
    # dict(zip(series, colors)) : 시리즈 이름 리스트와 색 리스트를
    # 나란히 짝지어({이름: 색, 이름: 색, ...}) 딕셔너리로 만든다.
    return dict(zip(series, colors))


def _select(lg: pd.DataFrame, full_sel: str, col: str) -> pd.DataFrame:
    """이미 melt된 롱 프레임(lg)에서 이 선택자에 해당하는 행만 추린다.

    full_sel : "leaf이름.컬럼이름[키값]"처럼 leaf 이름까지 포함한 전체
               문자열 — "series" 컬럼의 실제 값(예: "bar.close[005930]")
               과 정확히 이 형태로 만들어지므로, 그대로 비교해야 한다
               (leaf 이름을 뗀 "close[005930]"만으로는 절대 안 맞는다).
    col      : leaf 이름을 뗀 나머지("close" 또는 "close[005930]") —
               위에서 시리즈 전체로 못 찾았을 때, 순수 컬럼 이름("close")
               과 맞춰서 그 컬럼의 모든 키(모든 종목 등)를 다 남긴다."""
    sub = lg[lg["series"] == full_sel]
    if len(sub):
        return sub
    return lg[lg["col"] == col]


def _apply_columns(lg: pd.DataFrame, leaf_name: str,
                   columns: Sequence[str] | None) -> "pd.DataFrame | None":
    """columns(전체 선택 목록) 중 이 leaf(leaf_name)에 해당하는 항목만
    골라, 이미 melt된 롱 프레임(lg)에서 그만큼만 추려 돌려준다.

    columns의 각 항목은 세 가지 형태를 섞어 쓸 수 있다:
        "close"                컬럼 이름만 — 그 컬럼을 가진 leaf에는
                                적용되고, 없는 leaf와는 아예 무관하다.
        "sma20@close.sma"      "leaf이름.컬럼이름" — 그 leaf의 그 컬럼
                                전체(키가 있으면 키별로 전부)만 남긴다.
        "bar.close[005930]"    "leaf이름.컬럼이름[키값]" — series()가
                                보여주는 시리즈 문자열 그대로. 그 leaf의
                                그 시리즈 하나만(예: 그 종목 하나만) 남긴다.

    이 leaf를 겨냥한 항목이 하나도 없으면 None을 돌려준다 — "이 leaf는
    건드리지 않는다(가진 시리즈 전부 그대로 그린다)"는 뜻이다."""
    if columns is None:
        return None
    parts: list[pd.DataFrame] = []
    matched = False
    for item in columns:
        if "." in item:
            # "leaf이름.컬럼이름" 또는 "leaf이름.컬럼이름[키값]" — 이름이
            # 지금 이 leaf와 일치할 때만 반영한다(다른 leaf를 겨냥한
            # 항목은 조용히 건너뛴다).
            name, _, rest = item.rpartition(".")
            if name == leaf_name:
                matched = True
                sub = _select(lg, item, rest)   # item 전체를 시리즈 값과 비교
                if len(sub):
                    parts.append(sub)
        elif item in lg["col"].values:
            # 컬럼 이름만 적은 항목 — 이 leaf가 그 컬럼을 갖고 있을
            # 때만 반영한다(그 컬럼의 모든 키를 다 남긴다).
            matched = True
            sub = lg[lg["col"] == item]
            if len(sub):
                parts.append(sub)
    if not matched:
        return None
    if not parts:
        return lg.iloc[0:0]   # 매치는 됐지만(이름은 맞음) 실제 데이터가 없는 경우 — 빈 프레임
    # 같은 행이 여러 항목에 겹쳐 들어올 수 있어(예: "close"와 "bar.close"를
    # 동시에 적은 경우) drop_duplicates로 정리한다.
    return pd.concat(parts).drop_duplicates()


def resolve(longs: dict, sel: str):
    """선택자 -> (롱 프레임, 표시 라벨).

    "price"               그 name 의 모든 값 컬럼
    "price.close"         그 name 의 특정 컬럼만(모든 키 포함)
    "price.close[005930]" series()가 보여주는 시리즈 문자열 그대로 —
                          그 컬럼의 그 키 하나만(예: 그 종목 하나만)
    """
    if sel in longs:
        # sel이 그대로 어떤 Leaf 이름과 일치하면(예: "indicator@MACD"),
        # 그 leaf 전체를 그린다. 라벨(축 이름표)로는 "@label" 없이
        # table 부분만 쓴다(Name.parse(sel).table).
        return longs[sel], Name.parse(sel).table
    # str.rpartition(".") : 문자열을 오른쪽에서부터 "."을 기준으로
    # (앞부분, ".", 뒷부분) 으로 나눈다. "bar.close" 라면
    # name="bar", rest="close" 가 된다("."이 없으면 name은 빈 문자열).
    name, _, rest = sel.rpartition(".")
    if name in longs:
        sub = _select(longs[name], sel, rest)   # sel 전체를 시리즈 값과 비교
        if len(sub):
            # "close[005930]"처럼 시리즈 값이면 "[" 앞부분(컬럼 이름)만
            # 축 이름표로 쓴다 — 종목 태그까지 y축 라벨에 넣을 필요는 없다.
            label = rest.split("[")[0]
            return sub, label
    # 여기까지 왔다는 건 sel이 어떤 leaf 이름과도, "leaf.컬럼[키]" 형태와도
    # 일치하지 않았다는 뜻 — 오타 등으로 잘못된 이름을 썼을 가능성이
    # 크므로, 그림을 그리다 애매하게 실패하지 말고 바로 명확한 에러를
    # 낸다(사용 가능한 이름 목록도 같이 보여줘서 고치기 쉽게 한다).
    raise KeyError(
        f"layout 선택자 '{sel}' 를 찾을 수 없습니다. 사용 가능한 name: {list(longs)} "
        "(정확한 컬럼/시리즈 이름은 Tdata.series()로 확인할 수 있다)"
    )


def _kind_for(sel: str, kind: Kind) -> str:
    """선택자(sel)를 어떤 형태로 그릴지 결정한다.
    kind가 문자열 하나면 전부 그 형태고, 딕셔너리면 그 선택자에 해당하는
    값을 찾아 쓰되 없으면 "line"(기본)으로 떨어진다."""
    if isinstance(kind, str):
        return kind
    return kind.get(sel, "line")


# 캔들 하나당 이 정도 폭(인치)은 있어야 몸통/꼬리가 뭉개지지 않고
# 눈으로 구분된다 — 기본 figsize가 (line/mark/bar 기준으로 정해진)
# 고정폭이라, 캔들이 많으면 다닥다닥 뭉쳐서 그냥 회색 덩어리로 보이는
# 문제가 있었다. 캔들 개수에 맞춰 폭을 늘려주면 해결된다.
_CANDLE_INCH_PER_BAR = 0.09
_CANDLE_MAX_WIDTH = 60   # 데이터가 아주 많아도 그림이 한없이 커지지 않도록 상한


def _candle_count(longs: dict, rows: "Layout", kind: Kind) -> int:
    """rows 안에 kind="candle"로 그릴 선택자가 있으면, 그중 캔들 개수가
    가장 많은 것의 개수를 돌려준다(기본 figsize 폭 계산용). 하나도
    없으면 0 — 이때는 지금까지처럼 고정폭을 그대로 쓴다."""
    best = 0
    for row in rows:
        for side in row:
            for sel in side:
                if _kind_for(sel, kind) == "candle":
                    lg, _ = resolve(longs, sel)
                    best = max(best, lg[TIME].nunique())
    return best


def _bar_width(times: pd.Series) -> float:
    """datetime x축에 막대를 그릴 때 matplotlib이 이해하는 폭을 추정한다.

    초보자 참고: matplotlib은 날짜를 내부적으로 "그 날짜가 며칠째인지"를
    나타내는 실수로 다룬다. 그래서 막대 폭도 "며칠 치 너비인지"를 실수로
    줘야 한다(예: 1분봉이면 하루의 1/1440 정도). 데이터가 보통 몇 초/몇
    분 간격인지 스스로 알아내려고, 실제 시각들 사이의 간격 중 중앙값을
    쓴다(맨 앞 값 하나만 보면 하필 그 지점이 이상한 값일 수 있어서)."""
    times = pd.Series(times).sort_values()
    diffs = times.diff().dropna()
    if diffs.empty:
        return 1.0
    step = diffs.median()
    # 막대 사이에 살짝 여백이 남도록 실제 간격의 80%만 폭으로 쓴다.
    return (step / pd.Timedelta(days=1)) * 0.8


_OHLC = ("open", "high", "low", "close")


def _draw_candle(ax, lg: pd.DataFrame) -> None:
    """(t, keys..., col, value, series) 롱 포맷을 캔들차트로 그린다.

    캔들 하나는 open/high/low/close 네 값이 "같은 시각의 한 묶음"으로
    나란히 있어야 뜻이 있다 — 그런데 지금 lg는 melt돼서 그 네 값이
    서로 다른 행(col="open"인 행, col="high"인 행, ...)으로 흩어져
    있다. 그래서 그리기 전에 pivot으로 다시 "한 시각 = 한 행, 컬럼 =
    open/high/low/close"인 넓은 표로 되돌린다(id_vars로 남아있던 키
    컬럼 덕분에 lg 안에 그 정보가 그대로 있다)."""
    # TIME/col/value/series를 뺀 나머지가 이 leaf의 키 컬럼(예: symbol)
    # 이다 — to_long()이 id_vars로 넣어둔 것이라 melt에서도 안 없어졌다.
    key_cols = [c for c in lg.columns if c not in (TIME, "col", "value", "series")]
    if key_cols:
        # 키가 있는데 값이 두 종류 이상 섞여 있으면(예: 종목이 둘 이상)
        # 캔들 하나에 서로 다른 종목의 open/high/low/close가 뒤섞여
        # 버린다 — 그리는 대신 바로 에러를 내서 사용자가 종목을 하나로
        # 좁히게 안내한다.
        n_keys = lg[key_cols].drop_duplicates().shape[0]
        if n_keys > 1:
            raise ValueError(
                "캔들차트(kind='candle')는 한 번에 하나(예: 종목 하나)만 그릴 수 있습니다. "
                "columns=['leaf이름.컬럼이름[키값]']처럼 하나로 좁혀서 다시 시도하세요."
            )
    wide = lg.pivot_table(index=TIME, columns="col", values="value", aggfunc="last")
    missing = [c for c in _OHLC if c not in wide.columns]
    if missing:
        raise ValueError(
            f"캔들차트(kind='candle')를 그리려면 open/high/low/close 컬럼이 다 있어야 "
            f"합니다(이 leaf에 없는 컬럼: {missing})."
        )
    wide = wide.dropna(subset=list(_OHLC))
    if wide.empty:
        return

    up = wide["close"] >= wide["open"]
    # 흔히 쓰는 상승(초록)/하락(빨강) 관례색. up이 True인 자리는 up_color,
    # False인 자리는 down_color를 고른다.
    up_color, down_color = "#26a69a", "#ef5350"
    colors = np.where(up, up_color, down_color)

    width = _bar_width(pd.Series(wide.index))
    # 몸통(body) : open~close 사이를 막대로. bottom=두 값 중 작은 쪽,
    # height=그 차이 — 그러면 막대가 정확히 open과 close 사이를 채운다.
    body_bottom = wide[["open", "close"]].min(axis=1)
    body_height = (wide["close"] - wide["open"]).abs()
    ax.bar(wide.index, body_height, bottom=body_bottom, width=width,
          color=colors, zorder=3)
    # 꼬리(wick) : low~high를 얇은 세로선으로. ax.vlines(x, y시작, y끝)은
    # x 하나마다 세로선 하나씩 긋는 matplotlib 함수다.
    ax.vlines(wide.index, wide["low"], wide["high"], color=colors,
             linewidth=1, zorder=2)


def _draw_series(ax, lg, kind: str, cmap) -> None:
    """이미 (t, value, series) 롱 포맷으로 정리된 데이터 한 묶음을 kind에
    따라 축(ax)에 그린다. "series" 컬럼 값마다 다른 색으로 구분한다."""
    palette = {s: cmap[s] for s in lg["series"].unique()}

    if kind == "line":
        # sns.lineplot이 아니라 matplotlib의 ax.plot()을 series별로 직접
        # 부른다(bar와 같은 방식). 이유: seaborn의 lineplot/scatterplot
        # 계열은 그리기 전에 x/y/hue 중 NaN이 하나라도 있는 행을 내부에서
        # 통째로 걸러낸다(estimator=None/errorbar=None을 꺼도 이 전처리는
        # 그대로 남는다) — 그러면 time_frame()이 끊긴 구간을 표시하려고
        # 일부러 넣어둔 NaN이 seaborn 안에서 조용히 사라져서, 양옆의 점이
        # 그냥 실선으로 이어져 버린다(끊김이 안 보이는 실제 원인이었다).
        # matplotlib은 반대로 y가 NaN인 지점에서 선을 그냥 끊어 그린다 —
        # 그래서 NaN을 있는 그대로 넘기는 이 방식이라야 dropna=False가
        # 실제로 "끊어서 보여주기"로 이어진다.
        for s in lg["series"].unique():
            sub = lg[lg["series"] == s].sort_values(TIME)
            ax.plot(sub[TIME], sub["value"], color=cmap[s], label=s, linewidth=1.1)
    elif kind == "mark":
        # 선으로 안 잇고 점만 찍는다 — 체결가처럼 "그 순간에 그 값이
        # 있었다"만 보여주고 싶을 때, 또는 신호/트리거처럼 띄엄띄엄
        # 있는 값을 선으로 이으면 오히려 오해를 살 때 쓴다.
        sns.scatterplot(
            data=lg, x=TIME, y="value", hue="series", palette=palette,
            ax=ax, legend=True, s=18,
        )
    elif kind == "bar":
        # seaborn의 barplot은 x축을 "범주형"으로 다뤄서 연속된 시간축에는
        # 안 맞는다 — matplotlib의 ax.bar()를 직접, series(hue)별로 한
        # 번씩 나눠 부른다.
        width = _bar_width(lg[TIME])
        for s in lg["series"].unique():
            sub = lg[lg["series"] == s]
            ax.bar(sub[TIME], sub["value"], width=width, color=cmap[s],
                  label=s, alpha=0.7)
    elif kind == "candle":
        _draw_candle(ax, lg)
    else:
        raise ValueError(f"모르는 kind: {kind!r} (알려진 것: {', '.join(_KINDS)})")


def _draw(ax, longs, names, cmap, kind: Kind):
    """축(ax) 하나에 names에 적힌 시리즈들을 겹쳐 그린다. 각 이름이 어떤
    형태(line/mark/bar)로 그려질지는 kind가 정한다(_kind_for 참고)."""
    drawn, labels = False, []
    for n in names:
        lg, lab = resolve(longs, n)
        labels.append(lab)
        _draw_series(ax, lg, _kind_for(n, kind), cmap)
        drawn = True
    if drawn:
        # dict.fromkeys(labels) : 리스트에서 중복을 없애되 순서는 유지하는
        # 흔한 트릭이다(딕셔너리의 키는 중복될 수 없다는 성질을 이용).
        # 그렇게 중복 제거한 라벨들을 " / "로 이어 붙여 y축 이름으로 쓴다.
        ax.set_ylabel(" / ".join(dict.fromkeys(labels)))
    return drawn


def _merge_legend(ax_l, ax_r):
    """왼쪽 축과 오른쪽 축(twinx로 만든 보조 축)의 범례(legend)를 하나로
    합쳐서 왼쪽 위에 보기 좋게 몰아준다. 안 하면 왼쪽/오른쪽 범례가
    따로 두 군데에 떠서 지저분해진다."""
    handles, labels = ax_l.get_legend_handles_labels()
    if ax_l.get_legend():
        ax_l.get_legend().remove()
    if ax_r is not None:
        h2, l2 = ax_r.get_legend_handles_labels()
        if ax_r.get_legend():
            ax_r.get_legend().remove()
        handles, labels = handles + h2, labels + l2
    if handles:
        ax_l.legend(handles, labels, loc="upper left", fontsize=8, framealpha=0.85)


def list_series(td: Tdata, by: Sequence[str] | None = None) -> list[str]:
    """plot()을 부르면 실제로 그려질 시리즈 이름들을 미리 본다.

    Tdata.series()가 이 함수를 그대로 부른다 — plot()에 뭘 넘겨야 할지
    감이 안 잡힐 때, 여기서 나온 문자열을 그대로 복사해서 columns=나
    layout=에 쓸 수 있다:

        for s in td.series():
            print(s)
        # ...
        td.plot(columns=["bar.close[005930]"])   # 방금 본 문자열 그대로

    dropna=False로 melt한다(값이 전부 NaN이라 지금은 안 보이는 시리즈도
    "그릴 수 있는 이름" 목록에서는 빠지지 않게 하려는 것)."""
    leaves: list[Leaf] = list(td.staged(by))
    out: list[str] = []
    for lf in leaves:
        for s in to_long(lf, dropna=False)["series"].unique():
            if s not in out:
                out.append(s)
    return out


def plot_tdata(td: Tdata, layout: Layout | None = None, sync: str | None = None,
               sync_num: "int | None" = None,
               by=None, figsize=None, height_ratios=None, palette="tab10",
               show: bool = True, columns: Sequence[str] | None = None,
               dropna: bool = True, kind: Kind = "line",
               theme: "str | None" = "darkgrid", dedup: "str | None" = None):
    """Tdata 를 그린다. (fig, axes) 반환.

    sync/sync_num : 그리기 전에 Tdata.time_sync()로 시간축을 맞춘다 —
           time_sync(num=sync_num, how=sync)를 그대로 부르는 것과 같다.
           sync=None이고 sync_num도 None이면 정렬하지 않는다(sharex로
           시각적 정렬은 이미 됨). sync="inner"/"ffill"은 "어떻게"
           맞출지(그 시각에 값이 아예 없을 때 어떻게 할지), sync_num은
           "누구를" 기준으로 맞출지(None=기준 데이터, 정수면 그
           번째 others)다 — 자세한 뜻은 Tdata.time_sync() 참고.
    dedup : None(기본)이면 손대지 않는다. "last"/"first"/"max"/"min"을
           주면, 같은 (t,*키)에 행이 여러 개 있는 leaf(tick/quote처럼
           한 시간 안에 실제로 여러 사건이 있을 수 있는 데이터)를 그
           방식으로 하나씩 줄인 뒤에 그린다 — 그리기 전에 Tdata.staged
           (dedup=...)를 거치는 것과 같다. sync/sync_num(빈 자리를
           채우는 문제)과는 다른 문제라는 점에 주의 — dedup은 "값이
           이미 여러 개 있을 때" 대표값을 고르는 것이다.
    show : True(기본)면 마지막에 plt.show() 까지 불러 창을 띄운다 — 콘솔에서
           스크립트로 바로 실행할 때(예: python test/tdata.py) fig/axes를
           안 받아도 그림이 뜨게 하기 위함이다. 노트북처럼 자동으로 그려주는
           환경이거나, fig를 더 손본 뒤 직접 show()할 거면 False로 끈다.
    columns : (선택) 특정 컬럼만 그리고 싶을 때 이름 목록을 준다. 두 가지
           형태를 섞어 쓸 수 있다.
               "close"                컬럼 이름만 — 그 컬럼을 가진 leaf에는
                                      적용되고, 없는 leaf와는 아예 무관하다.
               "sma20@close.sma"      "leaf이름.컬럼이름" — 그 leaf의 그
                                      컬럼 전체(키가 있으면 키별로 전부)만.
               "bar.close[005930]"    "leaf이름.컬럼이름[키값]" — Tdata.
                                      series()가 보여주는 문자열 그대로.
                                      그 leaf의 그 시리즈 하나만(예: 여러
                                      종목 중 그 종목 하나만) 남긴다.
           예: columns=["sma20@close.sma"] — 여러 leaf를 체인으로 잔뜩
           쌓아뒀어도, "sma20@close"라는 이름의 leaf만 sma 컬럼 하나로
           좁혀지고 **나머지 leaf는 전부 원래대로(모든 값 컬럼) 그려진다**
           — columns에서 아예 언급되지 않은 leaf는 안 건드린다는 뜻이다.
           None(기본)이면 지금까지처럼 leaf마다 모든 값 컬럼을 다 그린다.
           (한 leaf가 columns에 걸려서 그릴 게 하나도 안 남으면 그 leaf는
           자동 배치(layout 생략 시)에서 빠진다 — 예: columns=["value"]인데
           bar Leaf처럼 "value"라는 컬럼이 없으면 그 leaf만 빠지고, 다른
           leaf는 여전히 온전하다.)
           어떤 컬럼/시리즈 이름을 쓸 수 있는지 미리 보려면 plot() 전에
           Tdata.series()를 불러본다.
    dropna : True(기본)면 값이 없는(NaN) 지점을 그리기 전에 지운다.
           Tdata.time_frame()으로 일부러 빈 구간을 NaN으로 남겨서 그
           구간을 시각적으로 끊어 보여주고 싶다면 False로 준다 — 지우지
           않고 그대로 두면 matplotlib이 NaN 지점에서 선을 끊어 그린다.
    kind : 무엇을 어떻게 그릴지. 기본은 "line"(전부 선)이다.
           "mark"/"bar" 처럼 문자열 하나를 주면 전부 그 형태로 그리고,
           {"이름": "형태", ...} 딕셔너리를 주면 선택자별로 다르게
           그린다(적히지 않은 선택자는 "line"). 선택자 문법은 layout과
           같다("bar_exceed10@close" 나 "bar@close.close" 처럼 "name"
           또는 "name.column").
               kind="mark"                                  전부 점으로
               kind={"bar_exceed10@close": "bar"}            이 이름만 막대로
    theme : seaborn 배경 스타일 — "darkgrid"(기본, 회색 격자)/"whitegrid"/
           "dark"/"white"/"ticks" 중 하나. None을 주면 스타일을 전혀
           건드리지 않는다(그 시점의 matplotlib 기본 배경 그대로).
           이 함수를 부르는 동안만 적용되고 끝나면 원래대로 돌아간다
           (sns.set_theme()처럼 전역으로 계속 남는 게 아니다) — 그래야
           이 라이브러리 바깥에서 matplotlib을 다르게 쓰고 있어도 그
           설정을 건드리지 않는다.
    """
    if sync is not None or sync_num is not None:
        td = td.time_sync(num=sync_num, how=sync if sync is not None else "inner")

    # kind 딕셔너리에 오타/알 수 없는 형태가 섞여 있으면 그리기 전에
    # 바로 알려준다(layout 선택자를 미리 검증하는 것과 같은 이유).
    if isinstance(kind, dict):
        bad = [k for k in kind.values() if k not in _KINDS]
        if bad:
            raise ValueError(f"모르는 kind: {bad} (알려진 것: {', '.join(_KINDS)})")
    elif kind not in _KINDS:
        raise ValueError(f"모르는 kind: {kind!r} (알려진 것: {', '.join(_KINDS)})")
    if theme is not None and theme not in _THEMES:
        raise ValueError(f"모르는 theme: {theme!r} (알려진 것: {', '.join(_THEMES)})")

    # td.staged(by, dedup) : 같은 이름의 Leaf가 여러 장이면 하나로 합치고,
    # dedup이 켜져 있으면 leaf마다 (t,*키) 중복까지 하나씩으로 줄인다.
    leaves: list[Leaf] = list(td.staged(by, dedup))
    # 먼저 leaf마다 전체를 다 melt한다(columns 적용 전) — columns가
    # "leaf.컬럼[키값]"(series() 문자열)까지 가리킬 수 있어서, 실제 키
    # 값을 보려면 melt가 끝난 뒤여야 한다(멜트 전 Leaf만 봐서는 어떤
    # 종목들이 있는지 알 수 없다). 그 다음 _apply_columns로 leaf마다
    # columns 중 자기 몫만 추린다 — 언급되지 않은 leaf는 None이 나와서
    # 전체를 그대로 쓴다.
    full_longs = {lf.name: to_long(lf, dropna=dropna) for lf in leaves}
    longs = {}
    for lf in leaves:
        filtered = _apply_columns(full_longs[lf.name], lf.name, columns)
        longs[lf.name] = full_longs[lf.name] if filtered is None else filtered
    if layout is not None:
        rows = list(layout)
    else:
        # columns 필터 때문에 그릴 값이 하나도 안 남은 leaf(예: columns=
        # ["value"]인데 bar처럼 "value" 컬럼이 없는 leaf)는 자동 배치에서
        # 빼둔다 — 안 그러면 빈 축(선이 하나도 없는 단)이 그대로 생긴다.
        plottable = [lf.name for lf in leaves if not longs[lf.name].empty]
        rows = default_layout(plottable)

    for row in rows:                      # 잘못된 선택자는 그리기 전에 터뜨린다
        for side in row:
            for sel in side:
                # 여기서는 결과를 안 쓰고 resolve()만 미리 불러본다 —
                # 존재하지 않는 이름이 layout에 섞여 있으면, 그림을 반쯤
                # 그리다 실패하는 대신 시작하기 전에 바로 에러를 내기
                # 위한 "사전 검증" 단계다.
                resolve(longs, sel)

    cmap = _color_map(longs, palette)
    n_rows = max(len(rows), 1)
    if figsize is None:
        # 기본 가로 폭 11인치 — 다만 캔들차트가 섞여 있으면 캔들 개수에
        # 맞춰 늘린다(안 그러면 캔들이 다닥다닥 뭉쳐서 형태를 알아볼 수
        # 없어진다). 캔들이 없으면 지금까지처럼 고정폭 그대로다.
        n_candles = _candle_count(longs, rows, kind)
        width = max(11, min(n_candles * _CANDLE_INCH_PER_BAR, _CANDLE_MAX_WIDTH))
        figsize = (width, 3.1 * n_rows)   # 세로 크기는 단(row) 개수에 비례하게 자동 계산

    # sns.axes_style(theme) : sns.set_theme()과 달리 전역 설정을 바꾸지
    # 않고, 이 with 블록 안에서만 그 스타일(배경색/격자선/테두리 등)을
    # 적용했다가 블록이 끝나면 원래 상태로 되돌리는 컨텍스트 매니저다.
    # theme=None이면 "지금 스타일 그대로"를 돌려주므로 사실상 아무것도
    # 안 바뀐다 — 그래서 분기 없이 항상 이 with로 감싸도 안전하다.
    # 축(Axes)의 배경/테두리는 만들어질 때(plt.subplots) 정해지므로,
    # 실제로 그림을 만들고 그리는 부분 전체를 이 안에 넣어야 한다.
    with sns.axes_style(theme):
        # plt.subplots(n_rows, 1, ...) : 세로로 n_rows개, 가로로 1개인
        # 격자(그러니까 "n_rows단짜리 세로 배열")의 그림(fig)과 축들(axes)을
        # 한 번에 만들어주는 matplotlib 함수.
        #   sharex=True   모든 단이 가로축(시간)을 공유해서, 확대/이동하면
        #                 다 같이 움직인다(여러 단을 비교하기 좋다).
        #   squeeze=False 단이 1개뿐이어도 axes를 항상 "표(2차원 배열)" 모양
        #                 그대로 돌려주게 강제한다(아래에서 a[0]로 접근하는
        #                 코드를 단이 몇 개든 똑같이 쓸 수 있게 하기 위해서).
        fig, axes = plt.subplots(
            n_rows, 1, sharex=True, figsize=figsize, squeeze=False,
            gridspec_kw={"height_ratios": height_ratios} if height_ratios else None,
        )
        # axes는 [[ax1], [ax2], ...] 모양(squeeze=False 때문에)이라, 리스트
        # 컴프리헨션으로 각 원소의 첫 번째 것만 꺼내 [ax1, ax2, ...] 처럼
        # 다루기 편한 1차원 리스트로 만든다.
        axes = [a[0] for a in axes]

        for ax_l, (left, right) in zip(axes, rows):
            _draw(ax_l, longs, left, cmap, kind)
            ax_r = None
            if right:
                # ax.twinx() : 같은 x축(시간)을 공유하면서, y축만 별도로 갖는
                # "오른쪽 보조 축"을 만든다. 왼쪽/오른쪽 규격(단위)이 다른
                # 두 시리즈(예: 가격과 거래량)를 한 단에 겹쳐 그릴 때 쓴다.
                ax_r = ax_l.twinx()
                ax_r.grid(False)   # 오른쪽 축의 격자선까지 그리면 겹쳐서 지저분하므로 끈다
                _draw(ax_r, longs, right, cmap, kind)
            _merge_legend(ax_l, ax_r)
            ax_l.set_xlabel("")   # 중간 단들은 x축 이름을 비워서, 맨 아래 단에만 한 번 표시한다

        axes[-1].set_xlabel(TIME)   # axes[-1] : 리스트의 "맨 마지막" 원소 — 즉 맨 아래 단

        if not dropna:
            # dropna=False(주로 time_frame() 직후)일 때만 x축 범위를 직접
            # 못 박는다. 이유: matplotlib은 y가 NaN인 점을 "데이터가
            # 없다"고 보고 축 자동 범위(autoscale) 계산에서 아예 빼버린다
            # — 그래서 앞/뒤로 NaN만 있는 구간(끊긴 데이터 이전/이후)은
            # 화면 밖으로 밀려나 버린다(선 자체는 NaN 지점에서 끊겨
            # 그려지지만, 그 지점이 애초에 화면에 안 보이면 소용없다).
            # leaf.df.index는 melt/dropna와 무관하게 항상 time_frame()이
            # 만든 전체 시간축 그대로이므로, 거기서 직접 전체 범위를
            # 구해 강제로 씌운다.
            t_values = [lf.df.index for lf in leaves if len(lf.df.index)]
            if t_values:
                t_min = min(idx.min() for idx in t_values)
                t_max = max(idx.max() for idx in t_values)
                for ax in axes:
                    ax.set_xlim(t_min, t_max)

        fig.tight_layout()          # 여러 축/제목/범례가 서로 겹치지 않도록 여백을 자동 조정
    if show:
        plt.show()   # 실제로 화면에 그림 창을 띄운다(창을 닫을 때까지 여기서 대기한다)
    return fig, axes
