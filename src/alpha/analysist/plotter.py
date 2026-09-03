"""Tdata 플로터 (seaborn).

축 배분 규칙
-----------
기본: staged 결과의 name 순서대로
      0번 -> 1단 왼쪽축, 1번 -> 1단 오른쪽축,
      2번 -> 2단 왼쪽축, 3번 -> 2단 오른쪽축, ...
지정: layout=[(왼쪽 name 리스트, 오른쪽 name 리스트), ...]
      한 축에 여러 name 을 넣으면 같은 y 규격으로 함께 그린다.

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

from typing import Sequence

import matplotlib.pyplot as plt   # 그림(figure)/축(axes)을 만들고 창을 띄우는 라이브러리
import seaborn as sns             # matplotlib 위에서 더 예쁜 선 그래프 등을 그려주는 라이브러리

from .tdata import TIME, Leaf, Name, Tdata, to_long

# Layout 은 "레이아웃(그림 배치)을 어떻게 지정하는지"에 대한 타입 별명이다.
# 실제 값은 이런 모양이다:
#     [ (["price"], ["volume"]),        # 1단: 왼쪽에 price, 오른쪽에 volume
#       (["indicator@MACD"], []) ]      # 2단: 왼쪽에 MACD, 오른쪽은 비움
# 즉, "(왼쪽에 그릴 이름들, 오른쪽에 그릴 이름들)" 튜플을 단(행)마다
# 하나씩 리스트로 나열한 것이 Layout이다.
Layout = Sequence[tuple[Sequence[str], Sequence[str]]]


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


def resolve(longs: dict, sel: str):
    """선택자 -> (롱 프레임, 표시 라벨).

    "price"        : 그 name 의 모든 값 컬럼
    "price.close"  : 그 name 의 특정 컬럼만  (규격 다른 컬럼을 축에서 분리할 때)
    """
    if sel in longs:
        # sel이 그대로 어떤 Leaf 이름과 일치하면(예: "indicator@MACD"),
        # 그 leaf 전체를 그린다. 라벨(축 이름표)로는 "@label" 없이
        # table 부분만 쓴다(Name.parse(sel).table).
        return longs[sel], Name.parse(sel).table
    # str.rpartition(".") : 문자열을 오른쪽에서부터 "."을 기준으로
    # (앞부분, ".", 뒷부분) 으로 나눈다. "bar.close" 라면
    # name="bar", col="close" 가 된다("."이 없으면 name은 빈 문자열).
    name, _, col = sel.rpartition(".")
    if name in longs:
        lg = longs[name]
        sub = lg[lg["col"] == col]   # 그 leaf 중에서도 "col" 컬럼 값이 일치하는 행만
        if len(sub):
            return sub, col
    # 여기까지 왔다는 건 sel이 어떤 leaf 이름과도, "leaf.컬럼" 형태와도
    # 일치하지 않았다는 뜻 — 오타 등으로 잘못된 이름을 썼을 가능성이
    # 크므로, 그림을 그리다 애매하게 실패하지 말고 바로 명확한 에러를
    # 낸다(사용 가능한 이름 목록도 같이 보여줘서 고치기 쉽게 한다).
    raise KeyError(
        f"layout 선택자 '{sel}' 를 찾을 수 없습니다. 사용 가능한 name: {list(longs)}"
    )


def _draw(ax, longs, names, cmap):
    """축(ax) 하나에 names에 적힌 시리즈들을 겹쳐 그린다."""
    drawn, labels = False, []
    for n in names:
        lg, lab = resolve(longs, n)
        labels.append(lab)
        # sns.lineplot(...) : 롱 포맷 데이터(lg)를 받아서 선 그래프를
        # 그리는 seaborn 함수.
        #   data=lg              그릴 데이터(롱 포맷)
        #   x=TIME, y="value"    가로축은 시간, 세로축은 값
        #   hue="series"         "series" 컬럼 값마다 다른 색의 선으로 구분
        #   palette=...          hue별 색을 위에서 미리 정한 cmap으로 고정
        #   estimator=None,
        #   errorbar=None        (t,*keys)가 유일함을 보장하므로(Leaf 설계),
        #                        같은 x값에 여러 y가 있을 때 하는 "평균 내기/
        #                        신뢰구간 그리기" 같은 집계 작업이 필요 없다
        #                        는 뜻으로 꺼둔 것 — 있는 그대로 선을 잇는다.
        #   ax=ax                이 축 위에 그린다
        sns.lineplot(
            data=lg, x=TIME, y="value", hue="series",
            palette={s: cmap[s] for s in lg["series"].unique()},
            estimator=None, errorbar=None,   # (t,*keys) 유일 보장 → 집계 불필요
            ax=ax, legend=True, linewidth=1.1,
        )
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


def plot_tdata(td: Tdata, layout: Layout | None = None, sync: str | None = None,
               by=None, figsize=None, height_ratios=None, palette="tab10",
               show: bool = True, columns: Sequence[str] | None = None):
    """Tdata 를 그린다. (fig, axes) 반환.

    sync : None 이면 정렬하지 않는다(sharex 로 시각적 정렬은 이미 됨).
           "inner"/"ffill" 을 주면 그리기 전에 기준 데이터 축으로 맞춘다.
    show : True(기본)면 마지막에 plt.show() 까지 불러 창을 띄운다 — 콘솔에서
           스크립트로 바로 실행할 때(예: python test/tdata.py) fig/axes를
           안 받아도 그림이 뜨게 하기 위함이다. 노트북처럼 자동으로 그려주는
           환경이거나, fig를 더 손본 뒤 직접 show()할 거면 False로 끈다.
    columns : (선택) 특정 컬럼만 그리고 싶을 때 이름 목록을 준다.
           예: columns=["close"] — bar Leaf에 open/high/low/close/volume이
           다 있어도 close 선 하나만 그린다. None(기본)이면 지금까지처럼
           leaf마다 모든 값 컬럼을 다 그린다. 여러 leaf가 섞여 있으면
           이 목록이 모든 leaf에 공통으로 적용되고(leaf.value_cols 와
           교집합), 그 leaf에 없는 이름은 조용히 무시된다 — 예를 들어
           columns=["value"]를 주면 indicator Leaf의 "value" 컬럼만
           그리고, bar Leaf처럼 "value"라는 컬럼이 없는 leaf는 그릴 게
           없어서 자동 배치(layout 생략 시)에서 아예 빠진다.
           특정 leaf에서만 특정 컬럼만 고르고 싶다면(leaf마다 다른 컬럼),
           이 옵션 대신 layout=[(["이름.컬럼"], [...]), ...] 형태로
           "leaf이름.컬럼이름" 선택자를 직접 쓰면 된다(resolve() 참고).
    """
    if sync is not None:
        td = td.time_sync(how=sync)

    # td.staged(by) : 같은 이름의 Leaf가 여러 장이면 하나로 합친 결과.
    leaves: list[Leaf] = list(td.staged(by))
    # 딕셔너리 컴프리헨션: {leaf 이름: 그 leaf를 롱 포맷으로 바꾼 것, ...}
    # 나중에 resolve()가 이름으로 바로 찾아 쓸 수 있도록 미리 다 바꿔둔다.
    # columns를 그대로 넘기면 to_long()이 그 이름들만 남기고 melt한다.
    longs = {lf.name: to_long(lf, columns=columns) for lf in leaves}
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
    figsize = figsize or (11, 3.1 * n_rows)   # 세로 크기를 단(row) 개수에 비례하게 자동 계산

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
        _draw(ax_l, longs, left, cmap)
        ax_r = None
        if right:
            # ax.twinx() : 같은 x축(시간)을 공유하면서, y축만 별도로 갖는
            # "오른쪽 보조 축"을 만든다. 왼쪽/오른쪽 규격(단위)이 다른
            # 두 시리즈(예: 가격과 거래량)를 한 단에 겹쳐 그릴 때 쓴다.
            ax_r = ax_l.twinx()
            ax_r.grid(False)   # 오른쪽 축의 격자선까지 그리면 겹쳐서 지저분하므로 끈다
            _draw(ax_r, longs, right, cmap)
        _merge_legend(ax_l, ax_r)
        ax_l.set_xlabel("")   # 중간 단들은 x축 이름을 비워서, 맨 아래 단에만 한 번 표시한다

    axes[-1].set_xlabel(TIME)   # axes[-1] : 리스트의 "맨 마지막" 원소 — 즉 맨 아래 단
    fig.tight_layout()          # 여러 축/제목/범례가 서로 겹치지 않도록 여백을 자동 조정
    if show:
        plt.show()   # 실제로 화면에 그림 창을 띄운다(창을 닫을 때까지 여기서 대기한다)
    return fig, axes
