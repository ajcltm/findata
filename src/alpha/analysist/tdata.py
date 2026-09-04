"""Tdata — 타임축을 인덱스로 갖는 데이터들의 컴포짓 컨테이너.

설계 요약
---------
- Leaf   : 이름 하나 + DataFrame 하나. 불변. 식별 단위는 (t, *keys).
- Tdata  : 기준 Leaf(_base) + 주입된 Leaf들(_others). 모든 연산은 새 Tdata 반환.
- 입력은 계약이다. 충돌 데이터는 add() 시점에 예외. 봉합은 사용자 몫.
- 파생(병합/싱크/리샘플)은 원본을 건드리지 않는다. staged()만 캐시.

■ 왜 이런 게 필요한가 (초보자를 위한 배경 설명)
    분석을 하다 보면 "종목 A의 봉 데이터"와 "종목 A의 지표 값"처럼 서로
    다른 시간 간격/구조를 가진 여러 데이터를 한 화면에 겹쳐 그리거나
    함께 다뤄야 할 때가 많다. 매번 pandas로 merge/concat/reindex 를
    직접 손으로 하면 실수하기 쉽고(시간축이 안 맞는데 억지로 합친다든가),
    코드도 매번 비슷한 걸 반복해서 짜게 된다.

    Tdata는 그 반복 작업을 클래스 뒤에 캡슐화한 것이다. "이름 하나 +
    DataFrame 하나"를 Leaf 라는 작은 단위로 감싸고, Tdata는 그런 Leaf를
    여러 장 겹쳐 들고 있으면서 시간축 맞추기(time_sync)/리샘플링
    (resample_frame)/끊긴 구간 대응(split_by_gap, time_frame)/그래프
    그리기(plot) 같은 공통 동작을 대신해준다.

■ "불변(immutable)"이라는 말의 뜻
    Leaf와 Tdata의 메서드들은 자기 자신을 바꾸지 않고, 대신 "바뀐 내용을
    담은 새 객체"를 만들어서 돌려준다(그래서 메서드 이름 옆에 "-> Tdata"
    같은 반환 타입이 계속 붙어 있다). 예를 들어 td.tslice(...) 를 불러도
    원래 td는 그대로고, 결과를 담은 새 Tdata를 변수에 받아써야 한다:

        td2 = td.tslice("2024-01-01", "2024-01-31")   # OK: 새 결과를 받음
        td.tslice("2024-01-01", "2024-01-31")          # 아무 효과 없음(결과를 버림)

    이렇게 만드는 이유는 "원본은 항상 안전하게 그대로 남아있다"는 걸
    보장하기 위해서다 — 실수로 원본 데이터를 건드릴 걱정 없이 마음껏
    이런저런 변환을 시도해볼 수 있다.
"""

from __future__ import annotations
# ↑ 타입 힌트를 문자열처럼 다뤄서, 클래스 정의 안에서 자기 자신의 이름을
#   미리 타입 힌트로 쓸 수 있게 해준다(예: Leaf 클래스 메서드가 "Leaf"를
#   반환 타입으로 쓰는 것). 실행 동작에는 영향이 없다.

from dataclasses import dataclass, field, replace
# ↑ dataclass 관련 도구들.
#   @dataclass       클래스에 __init__ 등을 자동으로 만들어주는 데코레이터.
#   field(...)       dataclass 필드의 기본값을 더 세밀하게 지정할 때 씀.
#   replace(obj, x=1) obj를 복사하면서 x만 새 값으로 바꾼 "새 객체"를 만든다.
from typing import Callable, Iterable, Mapping, Sequence
# ↑ 타입 힌트 도구들.
#   Callable[[인자들], 반환값]   "함수(또는 함수처럼 호출 가능한 것)"라는 뜻
#   Iterable[X]                 for로 돌릴 수 있는(반복 가능한) X들의 모음
#   Mapping[K, V]                dict처럼 "키로 값을 찾는" 자료형
#   Sequence[X]                  리스트/튜플처럼 순서가 있는 X들의 모음

import pandas as pd

TIME = "t"  # 모든 Leaf가 공유하는 시간 인덱스 이름
# ↑ 이렇게 "매직 넘버/매직 스트링"을 상수 하나로 빼두면, 나중에 인덱스
#   이름을 바꾸고 싶을 때 이 한 줄만 고치면 된다(코드 여기저기 흩어진
#   "t"라는 글자를 일일이 찾아 바꿀 필요가 없다).

# Tdata.time_frame(mode=...) 에서 쓰는, 사람이 읽기 쉬운 단위 글자 ->
# pandas가 pd.date_range(freq=...)에서 알아듣는 문자열 매핑.
# "m"(월)/"y"(년)은 "MS"/"YS"(Month/Year Start)를 써서, 매달/매년의
# 1일을 기준점으로 촘촘한 시간축을 만든다.
_TIME_FRAME_FREQ: dict[str, str] = {
    "s": "s",      # 초
    "min": "min",  # 분
    "h": "h",      # 시
    "d": "D",      # 일
    "m": "MS",     # 월(매월 1일)
    "y": "YS",     # 년(매년 1월 1일)
}


# --------------------------------------------------------------------------
# Name : "table@label" 파싱
# --------------------------------------------------------------------------
@dataclass(frozen=True)
# ↑ @dataclass 는 "이 클래스는 필드 몇 개를 가진 단순한 데이터 그릇이다"라고
#   선언하면, __init__/__repr__/__eq__ 같은 반복 코드를 파이썬이 자동으로
#   만들어주는 기능이다. frozen=True 를 추가하면 "한 번 만들면 필드 값을
#   못 바꾸는" 불변 객체가 된다(예: name.table = "x" 처럼 값을 다시
#   대입하려고 하면 에러가 난다) — 위 모듈 설명의 "불변" 원칙을 코드로
#   강제하는 장치다.
class Name:
    """table  = 축 배분/병합 그룹핑 단위, label = 같은 table 안의 변종 구분."""

    # dataclass에서는 이렇게 "이름: 타입" 줄이 곧 필드 선언이다.
    # 예를 들어 Name("indicator", "MACD") 라고 부르면 table="indicator",
    # label="MACD" 로 자동으로 채워진다(직접 __init__을 안 써도 됨).
    table: str
    label: str | None = None   # "= None" 은 기본값 — label을 안 주면 None이 된다

    @classmethod
    # ↑ @classmethod 는 "인스턴스가 아니라 클래스 자체(Name)로 호출하는
    #   메서드"라는 표시다. 첫 인자가 self가 아니라 cls(클래스 자신)인
    #   이유도 그래서다. Name.parse("indicator@MACD") 처럼 인스턴스를
    #   먼저 안 만들고도 바로 부를 수 있다 — 흔히 "이 클래스의 다른 방식
    #   생성자"를 만들 때 쓰는 패턴이다.
    def parse(cls, s: str) -> "Name":
        """"table@label" 형태의 문자열을 Name(table, label)로 쪼갠다.
        "@"가 없으면 label은 None이 된다(예: "indicator" → table="indicator").
        """
        # str.partition("@") : 문자열을 "@" 기준으로 (앞부분, "@", 뒷부분)
        # 3조각으로 나눈다. "@"이 아예 없으면 (전체, "", "") 이 된다.
        table, _, label = str(s).partition("@")
        # "@"이 없어서 label이 빈 문자열("")이면, "label or None" 은
        # 빈 문자열이 파이썬에서 "거짓(falsy)"으로 취급되므로 None이 된다.
        return cls(table, label or None)

    def __str__(self) -> str:
        # __str__ 을 정의해두면 str(name)이나 print(name), f"{name}" 처럼
        # 문자열로 변환될 때 이 함수가 실행된다.
        return f"{self.table}@{self.label}" if self.label else self.table


# --------------------------------------------------------------------------
# Leaf
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Leaf:
    """이름 하나에 대응하는 시계열 한 장.

    keys : 엔티티 구분 컬럼(예: ("symbol",)). 식별 단위는 (t, *keys).
           비어 있으면 시간축만으로 행이 유일해야 한다.
    agg  : 타임프레임 변경 시 컬럼별 집계 규칙. 미지정 컬럼은 "last".
    """

    name: str
    df: pd.DataFrame
    keys: tuple[str, ...] = ()
    # field(default_factory=dict) : 기본값으로 빈 딕셔너리 {}를 쓰고 싶을 때
    # 이렇게 쓴다. "agg: Mapping[str,str] = {}" 처럼 직접 {}를 기본값으로
    # 못 쓰는 이유는, 파이썬에서 가변(mutable) 객체를 기본값으로 바로
    # 쓰면 모든 인스턴스가 그 객체 하나를 공유해버리는 함정이 있어서다.
    # default_factory는 "인스턴스를 만들 때마다 새로 dict()를 호출해서
    # 각자 독립된 딕셔너리를 준다"는 뜻이다.
    agg: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        """dataclass가 자동으로 만들어준 __init__이 필드를 다 채운 "직후"에
        자동으로 한 번 더 호출되는 메서드다. 여기서 "값 검증"과 "정규화
        (형태를 통일시키는 작업)"를 한다 — 생성자 인자를 받은 그대로
        저장하지 않고, 한 번 더 손봐서 항상 일관된 모습으로 만든다."""
        # 이 클래스는 frozen=True라서 보통 self.keys = ... 처럼 값을
        # 새로 대입하면 에러가 난다. 그런데 "리스트로 줬어도 내부적으로는
        # 항상 튜플로 통일하고 싶다"처럼, 생성 시점에 딱 한 번은 값을
        # 정리해야 할 때가 있다. object.__setattr__(self, "필드", 값) 은
        # frozen 보호를 우회해서 "생성 중에는 예외적으로 딱 한 번" 값을
        # 세팅할 수 있게 해주는, dataclass 문서에 나오는 정석적인 방법이다.
        object.__setattr__(self, "keys", tuple(self.keys))
        df = self.df

        if not isinstance(df.index, pd.DatetimeIndex):
            # isinstance(x, 타입) : x가 그 타입(또는 그 자식 타입)인지 확인.
            # 여기서는 "df의 인덱스가 진짜 날짜/시간 인덱스인지" 검사해서,
            # 아니라면 나중에 이상한 곳에서 알기 힘든 에러가 나기 전에
            # 여기서 바로 명확한 에러 메시지로 멈춘다("계약 위반은 즉시
            # 알린다"는 모듈 설계 원칙).
            raise TypeError(f"[{self.name}] 인덱스가 DatetimeIndex가 아닙니다: {type(df.index)}")

        # 리스트 컴프리헨션으로 "keys에는 있다고 했는데 실제 df에는 없는
        # 컬럼"들을 찾아낸다. 하나라도 있으면 바로 에러를 낸다.
        missing = [k for k in self.keys if k not in df.columns]
        if missing:
            raise KeyError(f"[{self.name}] keys 컬럼 없음: {missing}")

        # df.copy(deep=False) : "얕은 복사" — 새 DataFrame 껍데기를 만들되,
        # 안의 실제 데이터(메모리 블록)는 원본과 공유한다. 이렬게 하는 이유는
        # 바로 아래에서 df.index를 바꿀 건데, 그게 원본 df에까지 영향을
        # 주면 안 되기 때문이다(호출한 쪽이 넘겨준 DataFrame은 그대로 두고
        # 싶다). 데이터 자체는 안 바꾸니 깊은 복사(deep=True)까지는 필요
        # 없어서 얕은 복사로 메모리와 시간을 아낀다.
        df = df.copy(deep=False)          # 경계에서 1회 방어. 데이터 블록은 공유.
        df.index = df.index.rename(TIME)  # 인덱스 이름을 항상 "t"로 통일한다
        if not df.index.is_monotonic_increasing:
            # is_monotonic_increasing : 인덱스가 오름차순으로 잘 정렬돼
            # 있는지 확인하는 pandas 속성. 정렬 안 돼 있으면 여기서 한 번
            # 정렬해서, 이후 슬라이싱(loc[start:end] 등)이 항상 안전하게
            # 동작하도록 만든다.
            df = df.sort_index()
        for k in self.keys:               # 키 컬럼은 categorical 로 (메모리/그룹핑)
            if not isinstance(df[k].dtype, pd.CategoricalDtype):
                # "categorical" 타입은 "005930", "000660"처럼 같은 값이
                # 반복해서 나오는 문자열 컬럼을 내부적으로 숫자 코드로
                # 저장해서 메모리를 아끼고, groupby 같은 연산도 더 빠르게
                # 해주는 pandas의 특수 자료형이다.
                df[k] = df[k].astype("category")
        object.__setattr__(self, "df", df)

    # -- 조회 -------------------------------------------------------------
    @property
    # ↑ @property 를 붙이면, 이 메서드를 leaf.parsed() 처럼 괄호를 붙여
    #   "호출"하지 않고 leaf.parsed 처럼 그냥 "속성"인 것처럼 값을 꺼낼 수
    #   있다. 매번 계산해서 보여주는 "계산된 속성"을 만들 때 쓰는 문법이다.
    def parsed(self) -> Name:
        """이 Leaf의 name("indicator@MACD" 같은 문자열)을 Name(table, label)
        객체로 풀어서 돌려준다."""
        return Name.parse(self.name)

    @property
    def value_cols(self) -> list[str]:
        """플롯/리샘플 대상 — key가 아니면서 숫자 컬럼만 고른다.
        strategy_id/trigger/order_id처럼 식별·주석용 문자열 컬럼은 시계열
        값이 아니므로 여기서 빠진다(섞어서 melt하면 seaborn이 문자/숫자가
        섞인 하나의 value 컬럼을 못 그린다). 필요하면 keys에 넣어 구분에
        쓰거나 .df로 원본 컬럼 그대로 꺼내 쓴다."""
        # pd.api.types.is_numeric_dtype(...) : 그 컬럼이 정수/실수 같은
        # "숫자" 타입인지 판별하는 pandas 도구. True인 것만 남긴다.
        return [c for c in self.df.columns
                if c not in self.keys and pd.api.types.is_numeric_dtype(self.df[c])]

    @property
    def times(self) -> pd.DatetimeIndex:
        """중복 제거된 정렬 시간축."""
        idx = self.df.index
        # 이미 시간이 유일하면(중복 없으면) 그대로 쓰고, 아니면 unique()로
        # 중복을 없앤 뒤 다시 정렬해서 돌려준다. time_sync()에서 "기준이
        # 되는 시각들의 목록"이 필요할 때 이 속성을 쓴다.
        return idx if idx.is_unique else pd.DatetimeIndex(idx.unique()).sort_values()

    def with_df(self, df: pd.DataFrame) -> "Leaf":
        """지금 이 Leaf와 이름/keys/agg는 같고, df만 바꾼 "새 Leaf"를 만든다.
        (frozen 이라 self.df = df 처럼 직접 못 바꾸니, dataclasses.replace로
        새 객체를 만들어 돌려주는 방식을 쓴다 — 모듈 맨 위의 "불변" 설명 참고.)"""
        return replace(self, df=df)

    def __repr__(self) -> str:
        # __repr__ 은 print(leaf)나 그냥 leaf라고 콘솔에 쳤을 때 보여줄
        # "개발자용 요약 문자열"을 정의하는 메서드다.
        k = f" keys={list(self.keys)}" if self.keys else ""
        return f"<Leaf {self.name} rows={len(self.df)} cols={self.value_cols}{k}>"


# --------------------------------------------------------------------------
# 식별/충돌
# --------------------------------------------------------------------------
def _ids(leaf_keys: Sequence[str]) -> list[str]:
    """"이 행을 유일하게 식별하는 컬럼들"의 목록 — 시간(t) + keys.
    예: keys=("symbol",) 이면 ["t", "symbol"] — 같은 시각(t)이라도
    종목(symbol)이 다르면 서로 다른 행으로 본다는 뜻이다."""
    # [TIME, *leaf_keys] : 리스트 안에서 *를 쓰면 "그 안의 원소들을 낱개로
    # 풀어서 끼워 넣는다"는 뜻이다(언패킹). leaf_keys=("symbol",) 이면
    # 결과는 ["t", "symbol"] 이 된다.
    return [TIME, *leaf_keys]


def _resolve_keys(leaf: Leaf, by: Sequence[str] | None) -> tuple[str, ...]:
    """by=None 이면 Leaf 가 선언한 keys, 아니면 호출 시점 지정값."""
    return leaf.keys if by is None else tuple(by)


def _flat(df: pd.DataFrame) -> pd.DataFrame:
    """Leaf를 거치지 않은 raw 프레임도 받도록 인덱스 이름을 정규화한 뒤 평탄화."""
    out = df.copy(deep=False)
    out.index = out.index.rename(TIME)
    # reset_index() : 인덱스로 세워져 있던 시간(t)을 다시 "평범한 컬럼"으로
    # 끌어내린다. 이러면 duplicated()/groupby() 같은 걸 "컬럼 이름"
    # 기준으로 다루기 편해진다(인덱스는 컬럼 취급이 조금 더 번거롭다).
    return out.reset_index()


def find_conflicts(df: pd.DataFrame, keys: Sequence[str] = ()) -> pd.DataFrame:
    """식별 키((t, *keys))는 같은데 값이 다른 행만 반환. 없으면 빈 프레임.

    모든 컬럼이 완전히 같은 행(재전송·경계 겹침)은 무손실이므로 충돌로 보지 않는다.
    """
    flat = _flat(df)
    # duplicated(keep="first") : "앞에서부터 봤을 때 이미 나온 적 있는
    # (모든 컬럼 값이 완전히 똑같은) 행"에 True 표시를 해주는 pandas 함수.
    # ~ 는 "부정(NOT)" — 그러니까 "완전 중복이 아닌 행만" 남긴다.
    flat = flat[~flat.duplicated(keep="first")]
    # 이번엔 "식별 키(t, *keys)만" 기준으로 중복 검사한다. keep=False 라서
    # 중복된 행 전부(처음 것도 포함해서)에 True가 찍힌다 — 완전 동일
    # 행은 위에서 이미 걸러졌으므로, 여기 걸리는 건 "키는 같은데 다른
    # 컬럼 값이 서로 다른", 즉 진짜 "충돌"인 행들이다.
    mask = flat.duplicated(subset=_ids(keys), keep=False).to_numpy()
    return flat[mask].set_index(TIME).sort_index()


def _dedup_exact(df: pd.DataFrame, keys: Sequence[str]) -> pd.DataFrame:
    """완전 동일행만 정리하고, 진짜 충돌이 남으면 예외."""
    flat = _flat(df)
    flat = flat[~flat.duplicated(keep="first")]
    conf = flat.duplicated(subset=_ids(keys), keep=False)
    if conf.any():
        # Series.any() : 그 Series(참/거짓 값들의 나열) 안에 True가
        # 하나라도 있으면 True를 돌려준다.
        bad = flat[conf.to_numpy()]
        raise ValueError(
            f"{len(bad)}행 충돌 (같은 (t,{','.join(keys) or '-'})에 다른 값). "
            f"예: {bad[TIME].iloc[0]} / .conflicts() 로 확인 후 정제해서 넣으세요."
        )
    return flat.set_index(TIME).sort_index()


# --------------------------------------------------------------------------
# Tdata
# --------------------------------------------------------------------------
class Tdata:
    """기준 Leaf + 주입된 Leaf들의 컴포짓. 모든 연산은 새 인스턴스를 낳는다.

    초보자 참고: "컴포짓(composite)"이란 "여러 개의 작은 것들을 하나로
    묶어서, 마치 하나인 것처럼 다루는" 디자인 패턴을 말한다. 여기서는
    Leaf(데이터 한 장) 여러 개를 Tdata 하나에 모아두고, plot()/
    time_sync() 같은 동작을 Tdata에 한 번만 걸면 안에 있는 모든 Leaf에
    일괄 적용되게 만든 것이다."""

    def __init__(self, base: Leaf, others: Iterable[Leaf] = (), resample_rule: str | None = None,
                segments: "tuple[Tdata, ...] | None" = None):
        # _base : 이 Tdata의 "기준"이 되는 데이터 한 장(예: 가격 봉).
        #         tslice/where/query 같은 필터는 전부 이 _base에만 적용된다.
        # _others : _base 말고 곁들여 붙인 데이터들(예: 지표, 체결 등).
        self._base = base
        # tuple(others) : 어떤 걸 넘겨받든(리스트든 다른 튜플이든) 내부
        # 저장 형태는 항상 튜플로 통일한다 — Leaf처럼 "한 번 만든 뒤엔
        # 안 바뀐다"는 걸 보장하려는 것.
        self._others = tuple(others)
        # resample_frame()으로 리샘플했다면 그때 쓴 규칙 문자열(예: "1min").
        # 이름을 "time_frame"이 아니라 "resample_rule"로 둔 이유: 아래
        # time_frame()이라는 메서드가 따로 있는데, 인스턴스 속성과 메서드
        # 이름이 겹치면 파이썬은 속성 쪽이 메서드를 완전히 가려버려서
        # td.time_frame(...)이 "메서드 호출"이 아니라 "속성값을 호출하려는
        # 시도"가 되어 버린다(그 속성이 문자열/None이면 "'str' object is
        # not callable" 같은 에러가 난다) — 실제로 이 실수를 했다가 바로
        # 잡았다.
        self.resample_rule = resample_rule
        self._staged_cache: tuple[Leaf, ...] | None = None   # staged() 결과를 기억해두는 캐시
        # split_by_gap()으로 시간 축이 끊긴 구간별로 쪼갠 적이 있으면, 그
        # 전체 구간 목록(자기 자신 포함)이 여기 담긴다. 평범하게 만든
        # Tdata는 None — "구간이라는 개념 자체가 없다"는 뜻이다.
        # Tdata는(Leaf/Name과 달리) frozen dataclass가 아니라 그냥 클래스라서,
        # split_by_gap()이 여러 개를 다 만든 "뒤에" 서로를 가리키게 이
        # 속성을 나중에 대입해도 문제없다(아래 split_by_gap() 참고).
        self._segments = segments

    # -- 생성 -------------------------------------------------------------
    @classmethod
    def from_df(cls, name: str, df: pd.DataFrame, keys=(), agg=None, resample_rule=None) -> "Tdata":
        """DataFrame 하나로부터 바로 Tdata를 만드는 지름길.
        Leaf를 직접 만들 필요 없이 Tdata.from_df("bar", df, keys=("symbol",))
        처럼 한 번에 쓸 수 있게 해준다(DataStore가 내부적으로 이 방식을 쓴다)."""
        return cls(Leaf(name, df, tuple(keys), agg or {}), (), resample_rule)

    def _spawn(self, base: Leaf, others: Iterable[Leaf], resample_rule=None) -> "Tdata":
        """"자기 자신과 같은 종류인데 base/others/resample_rule만 바뀐"
        새 Tdata를 만드는 내부 공용 도우미. 아래 여러 메서드(tslice,
        where, time_sync 등)가 전부 "새 Tdata를 만들어 반환"할 때 이걸
        거쳐가므로, 새로 만드는 방식을 한 곳에서만 관리할 수 있다.

        segments=self._segments 를 그대로 넘기는 게 핵심이다 — 이 덕분에
        split_by_gap()으로 한 번 구간을 나눈 뒤에는, tslice()/where()/
        time_sync() 등 뭘 체인으로 이어붙이든 "구간 목록을 기억하고 있다"
        는 상태가 자동으로 계속 따라온다(이 메서드들을 한 줄도 안 고쳐도
        된다 — 전부 결국 _spawn을 거쳐 가기 때문)."""
        return Tdata(base, others,
                    self.resample_rule if resample_rule is None else resample_rule,
                    segments=self._segments)

    # -- 구조 -------------------------------------------------------------
    @property
    def base(self) -> Leaf:
        """기준 Leaf 그 자체(DataFrame이 아니라 Leaf 객체)."""
        return self._base

    @property
    def df(self) -> pd.DataFrame:
        """기준 데이터의 프레임."""
        return self._base.df

    @property
    def leaves(self) -> tuple[Leaf, ...]:
        """그리기 순서 = 축 배분 순서. 내부 로직은 전부 이걸 쓴다."""
        # (self._base, *self._others) : 튜플 안에서 *로 _others를 풀어
        # 끼워 넣는다. 결과는 "기준 데이터가 맨 앞, 그다음 주입된 순서
        # 그대로"인 하나의 튜플이다.
        return (self._base, *self._others)

    def select(self, num: int) -> Leaf:
        """others 의 num 번째. -1 은 기준 데이터."""
        return self._base if num < 0 else self._others[num]

    # 이렇게 클래스 안에서 "메서드 이름 = 다른 메서드"로 대입해두면,
    # 파이썬이 특별하게 취급하는 이름(__getitem__)에 우리가 만든 select를
    # 그대로 연결한 것과 같다. __getitem__을 정의해두면 td[0]처럼
    # 대괄호 문법으로 select(0)을 부른 것과 똑같이 동작하게 된다.
    __getitem__ = select

    def __iter__(self):
        """for lf in td: 처럼 Tdata를 반복문에 바로 쓸 수 있게 해준다
        (내부적으로 leaves를 하나씩 꺼내준다)."""
        return iter(self.leaves)

    def __len__(self) -> int:
        """len(td) 를 하면 이 Tdata 안에 Leaf가 몇 장 들었는지 알려준다."""
        return len(self.leaves)

    # -- 주입 -------------------------------------------------------------
    def add(self, other: "Leaf | Tdata", by: Sequence[str] | None = None) -> "Tdata":
        """others 에 추가. 충돌 데이터면 여기서 즉시 예외."""
        # 넘어온 게 Tdata 통째로면 그 안의 leaves를 전부 풀어서 추가하고,
        # Leaf 하나만 왔으면 리스트 하나짜리로 감싼다 — 어느 쪽이든 아래
        # for 문은 "Leaf들의 리스트"만 상대하면 되게 통일하는 것이다.
        incoming = list(other.leaves) if isinstance(other, Tdata) else [other]
        for lf in incoming:
            bad = find_conflicts(lf.df, _resolve_keys(lf, by))
            if len(bad):
                raise ValueError(
                    f"[{lf.name}] {len(bad)}행 충돌. 정제 후 넣으세요. "
                    f"첫 충돌 시각: {bad.index[0]}"
                )
        return self._spawn(self._base, self._others + tuple(incoming))

    def __or__(self, other) -> "Tdata":
        """td1 | td2 처럼 "|" 연산자로 add()를 대신 쓸 수 있게 해준다.
        (파이썬에서 a | b 를 쓰면 자동으로 a.__or__(b) 가 호출된다.)"""
        return self.add(other)

    def conflicts(self, by: Sequence[str] | None = None) -> dict[str, pd.DataFrame]:
        """진단 전용. name -> 충돌 행. 비어 있으면 정상."""
        out = {}
        for name, group in _group_by_name(self.leaves).items():
            keys = _resolve_keys(group[0], by)
            merged = pd.concat([lf.df for lf in group]) if len(group) > 1 else group[0].df
            bad = find_conflicts(merged, keys)
            if len(bad):
                out[name] = bad
        return out

    # -- 병합(스테이징) ----------------------------------------------------
    def staged(self, by: Sequence[str] | None = None) -> tuple[Leaf, ...]:
        """동일 name Leaf들을 아래로 병합한 결과. 기본 경로만 캐시."""
        # 같은 이름으로 여러 번 add()된 적이 없다면 캐시를 그대로 재사용해서
        # 매번 다시 계산하지 않는다(속도 최적화). by를 직접 지정한 특수한
        # 호출은 캐시하지 않는다 — 매번 다른 결과가 나올 수 있어서다.
        if by is None and self._staged_cache is not None:
            return self._staged_cache

        out: list[Leaf] = []
        for name, group in _group_by_name(self.leaves).items():
            head = group[0]
            if len(group) == 1:
                # 같은 이름의 Leaf가 하나뿐이면 합칠 것도 없으니 그대로 쓴다.
                out.append(head)
                continue
            keys = _resolve_keys(head, by)
            merged = _dedup_exact(pd.concat([lf.df for lf in group]), keys)
            out.append(head.with_df(merged))

        result = tuple(out)
        if by is None:
            self._staged_cache = result
        return result

    # -- 시간축 -----------------------------------------------------------
    def time_sync(self, num: int | None = None, how: str = "inner") -> "Tdata":
        """타임축 정렬. num 미지정이면 기준 데이터, 지정하면 others[num] 기준.

        how="inner" : 기준 축에 존재하는 시각만 남김(교집합).
        how="ffill" : 기준 축으로 재인덱싱 후 직전 값 채움.
        """
        if how not in ("inner", "ffill"):
            raise ValueError("how 는 'inner' 또는 'ffill'")
        target = self.select(-1 if num is None else num)
        t = target.times
        # 리스트 컴프리헨션: 이 Tdata 안의 모든 leaf(기준+주입)를 하나씩
        # target의 시간축 t에 맞춰(_sync_leaf) 새로운 Leaf 목록을 만든다.
        synced = [_sync_leaf(lf, t, how) for lf in self.leaves]
        # synced[0]이 새 기준, synced[1:]("1번부터 끝까지"라는 슬라이싱
        # 문법)이 새 others가 된다.
        return self._spawn(synced[0], synced[1:])

    def resample_frame(self, rule: str) -> "Tdata":
        """리샘플링. 컬럼별 규칙은 Leaf.agg, 미지정은 last.

        초보자 참고: "리샘플링(resample)"이란 예를 들어 1초마다 있던
        데이터를 1분 단위로 뭉쳐서 다시 만드는 것을 말한다. rule은
        pandas가 이해하는 문자열이다(예: "1min", "5T", "1H").

        (예전 이름은 set_time_frame() 이었다 — "리샘플한다"는 동작이
        바로 드러나도록 이름을 바꿨다. self.resample_rule 속성에는
        여전히 이 rule 문자열이 남는다.)"""
        resampled = [_resample_leaf(lf, rule) for lf in self.leaves]
        return self._spawn(resampled[0], resampled[1:], resample_rule=rule)

    def time_frame(self, mode: str, start, end) -> "Tdata":
        """"정상적으로 끊김 없이 이어졌어야 할" 시간축을 사용자가 직접
        정해서, 그 위에 데이터를 얹는다. 원래 데이터가 없는 자리는
        ffill로 채우지 않고 그대로 비워(NaN) 둔다 — 그래야 나중에
        plot()에서 그 구간이 "값이 있었는데 이어붙인 것"처럼 보이지
        않고, 진짜로 빈 공간(끊긴 구간)으로 보인다.

        mode  : "s"(초) / "min"(분) / "h"(시) / "d"(일) / "m"(월) / "y"(년)
                중 하나 — 이 단위로 start부터 end까지 촘촘한 시간축을
                만든다.
        start, end : 그 시간축의 처음과 끝. 문자열("2026-09-03 09:00",
                "2026-01-01")이든 date/datetime이든 pandas가 알아서
                받는다.

        ★ 이 메서드는 "정상"이 뭔지 스스로 판단하지 않는다 ★
        공휴일을 뺄지, 장중 시간(09:00~15:30)만 볼지 같은 건 여기서
        전혀 신경 쓰지 않는다 — mode="d"면 토요일/일요일/공휴일 가리지
        않고 매일 하루씩, mode="h"면 새벽 시간대까지 매시간 다 채운
        시간축을 그냥 만든다. 그런 "진짜 정상 거래 시간"이 필요하면,
        사용자가 원하는 시각만 걸러낸 목록을 직접 계산해서 걸러 써야
        한다(지금은 mode/start/end로 만드는 균일한 시간축만 지원).

        예:
            td.time_frame(mode="d", start="2026-01-01", end="2026-02-28").plot(dropna=False)
            td.time_frame(mode="s", start="2026-09-03 09:00", end="2026-09-03 09:05").plot(dropna=False)

        plot()에서 이 빈 구간을 실제로 "끊어서" 보려면 dropna=False를
        같이 줘야 한다 — 기본값(dropna=True)은 NaN을 그리기 전에
        지워버려서, 지금 일부러 넣어둔 빈 구간 표시가 사라진다."""
        if mode not in _TIME_FRAME_FREQ:
            raise ValueError(
                f"모르는 mode: {mode!r} (알려진 것: {', '.join(_TIME_FRAME_FREQ)})"
            )
        # pd.date_range(start, end, freq=...) : start부터 end까지, freq
        # 간격으로 촘촘한 시각 목록(DatetimeIndex)을 만드는 pandas 함수.
        # 예: freq="D"면 하루 간격으로, freq="h"면 한 시간 간격으로.
        idx = pd.date_range(start, end, freq=_TIME_FRAME_FREQ[mode])
        framed = [_reindex_leaf(lf, idx, method=None) for lf in self.leaves]
        return self._spawn(framed[0], framed[1:])

    # -- 구간 분할(끊긴 녹화 대응) -------------------------------------------
    @property
    def segments(self) -> "tuple[Tdata, ...] | None":
        """split_by_gap()으로 나눈 전체 구간 목록(자기 자신 포함).
        한 번도 안 나눴으면 None이다. len(td.segments)로 몇 개로
        쪼개졌는지, td.segments[i].base.times[[0,-1]]로 각 구간이
        어느 시각부터 어느 시각까지인지 살펴볼 수 있다."""
        return self._segments

    def segment(self, i: int) -> "Tdata":
        """i번째 구간을, 그 구간이 처음 나뉘었을 때의 "원본 상태"로
        돌려준다 — 지금 self에 그동안 걸어둔 tslice()/where() 같은
        필터는 여기서 리셋된다(구간을 넘어갈 때마다 그 필터가 계속
        누적되면 예측하기 어려워지므로, 매번 그 구간의 순수한 데이터부터
        다시 시작하게 만들었다). 그 구간에서부터 새로 체인을 이어가면
        된다: td.segment(2).tslice(...).plot().

        split_by_gap()을 부른 적이 없어 segments가 None이면, 아직
        구간이라는 개념이 없다는 뜻이므로 에러를 낸다."""
        if self._segments is None:
            raise ValueError(
                "이 Tdata는 split_by_gap()으로 나눈 적이 없어 구간이 없습니다."
            )
        return self._segments[i]

    def split_by_gap(self, max_gap: "str | pd.Timedelta | None" = None,
                     factor: float = 10, sample: int = 200) -> "Tdata":
        """기준(base) 데이터의 시간축에서 "끊긴 지점"을 찾아 여러 구간으로
        나눈다. 녹화(Recorder)가 중간에 멈췄다 다시 시작하면 그 시간
        동안의 데이터가 통째로 비어서, 리샘플/이동평균/그래프 같은 걸
        이어서 계산하면 그 빈 구간을 마치 아무 일도 없었던 것처럼 뭉개
        버린다 — split_by_gap()은 그 끊긴 지점마다 데이터를 잘라서, 각
        구간을 서로 별개인 Tdata로 다루게 해준다.

        max_gap : 이 값보다 큰 시간 간격이 나오면 "끊겼다"고 본다.
                  "5min"처럼 pandas가 이해하는 문자열이나 pd.Timedelta를
                  준다. None(기본)이면 자동으로 판단한다 — 데이터 앞쪽
                  sample개 구간의 간격을 보고 "이 데이터는 초 단위인지
                  분 단위인지"부터 추정한 뒤(가장 자주 나온 간격 = 정상
                  간격), 그 정상 간격의 factor배보다 크면 끊긴 것으로 본다.

                  ★ 자동 판단의 한계(실측으로 확인한 것) ★
                  봉(bar)처럼 간격이 거의 일정한 데이터에는 이 방식이 잘
                  맞는다. 하지만 체결/호가처럼 원래도 간격이 들쭉날쭉한
                  데이터는 "정상 간격"의 폭 자체가 넓어서(가끔 2~4초씩
                  걸리는 것도 정상), factor를 너무 작게 잡으면 진짜
                  끊김이 아닌 곳까지 무더기로 끊긴 걸로 오판한다 — 실제
                  호가 9만여 행짜리 데이터로 재보니 factor=1.5에서는
                  8,911개 구간(대부분 오탐)이 나왔고, factor=10에서는
                  33개로 줄었다(수동으로 5분 임계값을 준 결과는 12개).
                  그래서 기본값을 넉넉하게(factor=10) 잡아뒀지만, 이런
                  불규칙한 데이터를 정밀하게 다뤄야 한다면 자동 판단에
                  기대지 말고 max_gap을 직접 주는 걸 권장한다.
        factor  : max_gap을 자동으로 잡을 때만 쓰는 배율(기본 10배).
        sample  : 자동 판단 시 "정상 간격"을 추정하려고 앞에서부터 볼
                  타임스탬프 간격 개수(기본 200개). 데이터 맨 앞부분이
                  이미 끊겨 있는 극단적인 경우에도, 최빈값(mode)을 쓰므로
                  대부분 정상 간격을 잘 잡아낸다.

        반환값은 "0번 구간"의 Tdata다 — 그래서 이 메서드 뒤에 바로
        .tslice()나 .plot() 같은 걸 체인으로 이어붙이면 자연스럽게 첫
        구간을 기준으로 계속된다. 특정 구간부터 이어가고 싶으면
        .segment(i)를 쓴다(예: td.split_by_gap("5min").segment(2)).

        base 뿐 아니라 add()로 곁들여둔 다른 leaf(others)들도 전부 같은
        구간 경계로 잘린다 — 그래야 한 구간 안에서는 봉/지표/체결 등이
        전부 "그 구간의 시간대" 것들로만 맞춰진다."""
        times = self._base.times
        boundaries = _find_segment_bounds(times, max_gap, factor, sample)

        segment_tdatas = []
        for start, end in boundaries:
            # 이 구간의 [start, end] 시간 범위로 base뿐 아니라 others까지
            # 전부 잘라낸다(tslice()는 base만 자르지만, 여기서는 구간
            # 안의 모든 데이터가 같은 시간대여야 하므로 leaves 전체를 돈다).
            sliced = [_tslice_leaf(lf, start, end) for lf in self.leaves]
            segment_tdatas.append(Tdata(sliced[0], sliced[1:], self.resample_rule))

        segments = tuple(segment_tdatas)
        # 다 만든 "뒤에" 서로를 가리키게 세팅한다 — 만드는 도중에는 아직
        # segments 튜플 자체가 존재하지 않으므로, 전부 만들고 나서 한
        # 바퀴 더 돌며 채워 넣는다. Tdata는 frozen이 아니라서 이렇게
        # 생성 후에 속성을 채워 넣어도 문제없다.
        for seg in segments:
            seg._segments = segments
        return segments[0]

    # -- 필터 (기준 데이터에만 적용, 계속 Tdata 반환) ------------------------
    def tslice(self, start=None, end=None) -> "Tdata":
        """시간 범위. 정렬된 인덱스 슬라이스라 복사가 거의 없다."""
        # df.loc[start:end] : 인덱스(시간) 기준으로 "start부터 end까지"를
        # 잘라내는 pandas 문법. 리스트의 [시작:끝] 슬라이싱과 비슷한데,
        # 여기서는 숫자 위치가 아니라 "시간 값" 기준이다.
        return self._spawn(self._base.with_df(self._base.df.loc[start:end]), self._others)

    def where(self, cond: Callable[[pd.DataFrame], pd.Series]) -> "Tdata":
        """cond(df) -> bool Series 로 기준 데이터를 거른다.

        예: td.where(lambda df: df["close"] > 70000)
        cond는 "DataFrame을 받아서, 각 행이 참/거짓인 Series를 돌려주는
        함수"다. df[bool_series] 형태로 조건에 맞는 행만 남긴다."""
        df = self._base.df
        return self._spawn(self._base.with_df(df[cond(df)]), self._others)

    def query(self, expr: str, **kw) -> "Tdata":
        """문자 비교 등 pandas query 표현식.

        예: td.query("close > 70000 and volume > 1000")
        where()와 비슷하지만, 조건을 함수 대신 문자열(SQL과 비슷한
        표현식)로 쓸 수 있어서 짧게 쓰고 싶을 때 편하다.
        **kw : query()에 더 넘기고 싶은 옵션이 있으면 그대로 전달한다
        (예: engine="python")."""
        return self._spawn(self._base.with_df(self._base.df.query(expr, **kw)), self._others)

    def head(self, n: int = 5) -> "Tdata":
        """기준 데이터의 앞에서 n개 행만 남긴 새 Tdata (내용을 훑어볼 때)."""
        return self._spawn(self._base.with_df(self._base.df.head(n)), self._others)

    # -- 표현 -------------------------------------------------------------
    def to_frame(self, by: Sequence[str] | None = None) -> pd.DataFrame:
        """스테이징 결과를 name 접두사 붙여 가로로 이어붙인 진단용 프레임."""
        parts = []
        for lf in self.staged(by):
            wide = _pivot_wide(lf)
            parts.append(wide)
        # pd.concat(parts, axis=1) : 여러 DataFrame을 "옆으로"(컬럼 방향,
        # axis=1) 이어 붙인다. axis=0(기본값)이면 "아래로"(행 방향) 이어
        # 붙는다는 차이를 기억해두면 pandas 다룰 때 계속 도움이 된다.
        return pd.concat(parts, axis=1).sort_index()

    def __repr__(self) -> str:
        body = "\n".join(f"  [{i}] {lf!r}" for i, lf in enumerate(self.leaves))
        # enumerate(seq) : 반복하면서 "몇 번째인지(0,1,2,...)"를 같이
        # 꺼내주는 파이썬 기본 함수. for i, lf in enumerate(leaves) 하면
        # i에 순번, lf에 그 leaf가 들어온다.
        tf = f" resample_rule={self.resample_rule}" if self.resample_rule else ""
        return f"<Tdata base={self._base.name} n={len(self)}{tf}>\n{body}"

    # -- 플롯 -------------------------------------------------------------
    def plot(self, layout=None, sync: str | None = None, by=None,
             figsize=None, height_ratios=None, palette="tab10", show: bool = True,
             columns: Sequence[str] | None = None, dropna: bool = True):
        """seaborn 으로 그린다. 자세한 규칙은 plotter.plot_tdata 참고.
        show=True(기본)면 plt.show() 까지 불러 콘솔에서 바로 창이 뜬다.
        columns=["close"] 처럼 주면 그 컬럼(들)만 그린다(선택 사항 —
        안 주면 지금까지처럼 모든 값 컬럼을 다 그린다).
        dropna=False로 주면 time_frame()으로 남겨둔 빈 구간(NaN)이
        지워지지 않고 그대로 그려져서, 끊긴 자리가 실제로 끊겨 보인다
        (기본 True는 예전처럼 NaN을 지우고 그린다)."""
        # 함수 맨 위가 아니라 여기, 메서드 "안"에서 import하는 걸 "지연
        # 임포트(lazy import)"라고 한다. plotter.py는 matplotlib/seaborn을
        # 불러오는데, 이건 그림을 그릴 때만 필요하지 데이터를 읽고 다룰
        # 때는(ticks()/indicators() 등) 전혀 필요 없다. 여기서만 import
        # 해두면, .plot()을 한 번도 안 부르는 스크립트는 무거운 그래프
        # 라이브러리를 아예 로딩하지 않아도 돼서 더 빠르게 시작한다.
        from .plotter import plot_tdata

        return plot_tdata(self, layout=layout, sync=sync, by=by, figsize=figsize,
                          height_ratios=height_ratios, palette=palette, show=show,
                          columns=columns, dropna=dropna)


# --------------------------------------------------------------------------
# 내부 헬퍼
# --------------------------------------------------------------------------
# 여기부터는 클래스 밖에 있는, 밑줄(_)로 시작하는 "내부 전용" 함수들이다.
# Leaf/Tdata의 메서드들이 재사용하는 계산 로직을 이름 붙여 빼둔 것으로,
# 바깥에서 직접 부를 일은 거의 없다(to_long()만 예외 — plotter.py에서 씀).
def _group_by_name(leaves: Sequence[Leaf]) -> dict[str, list[Leaf]]:
    """입력 순서(=그리기 순서)를 보존하며 name 별로 묶는다."""
    g: dict[str, list[Leaf]] = {}
    for lf in leaves:
        # dict.setdefault(키, 기본값) : 그 키가 이미 있으면 기존 값을
        # 그대로 돌려주고, 없으면 기본값(여기선 빈 리스트 [])을 넣어준
        # 뒤 그 값을 돌려준다. 매번 "키가 있는지 먼저 확인"하는 if문을
        # 안 써도 되게 해주는 pandas/딕셔너리의 흔한 관용구다.
        g.setdefault(lf.name, []).append(lf)
    return g


def _tslice_leaf(leaf: Leaf, start, end) -> Leaf:
    """leaf 하나를 [start, end] 시간 범위로 자른다(split_by_gap()이 base뿐
    아니라 others의 각 leaf에도 이 함수를 적용한다). Tdata.tslice()가
    base 하나에 대해 하는 것과 똑같은 계산을 leaf 하나 단위로 뺀 것이다."""
    return leaf.with_df(leaf.df.loc[start:end])


def _infer_normal_step(times: pd.DatetimeIndex, sample: int) -> "pd.Timedelta | None":
    """맨 앞 sample개 구간의 간격을 보고 "정상 간격"이 뭔지 추정한다.

    초보자 참고: times가 [09:00:00, 09:00:01, 09:00:02, ...] 처럼 1초
    간격이면, 연속한 값끼리의 차이(diff)도 거의 다 1초일 것이다. 그 중
    가장 자주 나오는 값(최빈값, mode)을 "이 데이터의 정상 간격"으로
    본다 — 맨 처음 한두 개 간격만 보면 그게 하필 진짜 끊긴 지점일 수도
    있어서, 여러 개를 보고 그중 다수결로 정하는 것이다.

    시각이 2개 미만이면(간격을 하나도 못 구하면) None을 돌려준다."""
    if len(times) < 2:
        return None
    # times[1:] - times[:-1] : 인덱스를 하나씩 밀어서 서로 빼면, 바로
    # 옆 시각끼리의 간격(Timedelta)들이 나온다. [:sample]로 앞에서부터
    # 정해준 개수만큼만 본다(전체를 다 보지 않아도 충분하고, 이 편이
    # 데이터가 아주 많을 때도 빠르다).
    diffs = (times[1:] - times[:-1])[:sample]
    if len(diffs) == 0:
        return None
    # pd.Series(...).mode() : 가장 자주 나오는 값(들)을 오름차순으로
    # 돌려주는 pandas 함수. 여러 값이 동률(같은 빈도)일 수도 있어서
    # 여러 개가 나올 수 있는데, 그중 제일 작은 것(iloc[0])을 쓴다.
    return pd.Series(diffs).mode().iloc[0]


def _find_segment_bounds(times: pd.DatetimeIndex, max_gap, factor: float,
                         sample: int) -> list[tuple]:
    """times(정렬된 시간축)를 끊긴 지점마다 잘라 (시작, 끝) 쌍의 목록으로
    돌려준다. 끊긴 지점이 하나도 없으면 [(전체 시작, 전체 끝)] 하나짜리
    목록이 된다(구간이 1개뿐인 것도 "나눴다"고 취급 — split_by_gap()의
    반환값이 항상 똑같은 모양이 되게 하려는 것)."""
    if len(times) == 0:
        return [(None, None)]
    if len(times) == 1:
        return [(times[0], times[0])]

    if max_gap is not None:
        threshold = pd.Timedelta(max_gap)
    else:
        normal_step = _infer_normal_step(times, sample)
        # 정상 간격을 아예 못 구했으면(극단적으로 데이터가 적으면) 끊긴
        # 곳을 못 찾는다는 뜻이므로, 전체를 구간 하나로 본다.
        if normal_step is None:
            return [(times[0], times[-1])]
        threshold = normal_step * factor

    diffs = times[1:] - times[:-1]
    # diffs[i] 는 times[i]와 times[i+1] 사이의 간격이다. 그게 threshold
    # 보다 크면 "times[i]까지가 한 구간의 끝, times[i+1]부터가 다음
    # 구간의 시작"이라는 뜻이다.
    break_after = [i for i, d in enumerate(diffs) if d > threshold]

    bounds = []
    start_idx = 0
    for i in break_after:
        bounds.append((times[start_idx], times[i]))
        start_idx = i + 1
    bounds.append((times[start_idx], times[-1]))
    return bounds


def _reindex_leaf(leaf: Leaf, idx: pd.DatetimeIndex, method: "str | None") -> Leaf:
    """leaf 하나를 새 시간축 idx로 재색인한다. method=None이면 원래
    없던 시각은 NaN으로 비워두고, method="ffill"이면 직전 값으로
    채운다. time_sync()의 ffill 모드와 time_frame() 이 공통으로 쓴다.

    keys가 있으면(예: symbol별로 여러 종목이 섞여 있으면) 종목마다
    따로따로 재색인해야 한다 — 안 그러면 ffill일 때 종목 A의 "직전 값"
    이 종목 B로 새거나, method=None이어도 서로 다른 종목의 시간축이
    한 표에 뒤섞여 버린다. 그래서 groupby로 종목별로 쪼갠 뒤 각각
    reindex를 적용하고 다시 합친다."""
    if leaf.keys:
        parts = []
        # observed=True : 카테고리 컬럼으로 groupby할 때, 실제 데이터에
        # 나타나지 않는 카테고리 값은 그룹으로 만들지 않는다(불필요한
        # 빈 그룹을 건너뛰어 더 빠르고 결과도 깔끔하다).
        for kv, g in leaf.df.groupby(list(leaf.keys), observed=True, sort=False):
            # groupby 결과에서 kv는 그 그룹의 키 값이다. keys가 하나뿐이면
            # kv가 그냥 값 하나("005930")로 오고, 여러 개면 튜플로 온다 —
            # 아래 zip(leaf.keys, kv) 에서 항상 튜플로 다루기 위해 통일한다.
            kv = kv if isinstance(kv, tuple) else (kv,)
            # reindex(idx, method=...) : 인덱스를 통째로 idx로 바꾼다.
            # method="ffill"이면 idx에는 있지만 원래 없던 시각을 "바로
            # 이전 값"으로 채우고, method=None이면 그 자리를 그냥 NaN으로
            # 비워둔다(끊긴 구간을 진짜 빈 것처럼 보이게 하고 싶을 때).
            g2 = g.drop(columns=list(leaf.keys)).reindex(idx, method=method)
            for col, val in zip(leaf.keys, kv):
                # zip(a, b) : 두 시퀀스를 짝지어 (a[0],b[0]), (a[1],b[1])...
                # 로 하나씩 묶어준다. 여기서는 "키 컬럼 이름"과 "그 그룹의
                # 키 값"을 짝지어서, reindex로 없어진 키 컬럼을 다시
                # 채운다(값 컬럼과 달리 키 컬럼은 NaN으로 비우지 않고
                # 그 그룹의 정체성을 그대로 유지시킨다).
                g2[col] = val
            parts.append(g2)
        df = pd.concat(parts).sort_index()
        for k in leaf.keys:
            df[k] = df[k].astype("category")
    else:
        df = leaf.df.reindex(idx, method=method)

    df.index = df.index.rename(TIME)
    return leaf.with_df(df)


def _sync_leaf(leaf: Leaf, t: pd.DatetimeIndex, how: str) -> Leaf:
    """leaf 하나를 시간축 t에 맞춘다(time_sync()가 각 leaf에 대해 이 함수를
    호출한다). how="inner"면 t에 있는 시각만 남기고, how="ffill"이면
    t의 모든 시각에 대해 값을 만들되 없는 시각은 "직전 값"으로 채운다."""
    if how == "inner":
        # index.isin(t) : 인덱스의 각 시각이 t 안에 있는지 참/거짓으로
        # 알려준다. 그걸로 df를 걸러서, t에도 있는 시각의 행만 남긴다.
        return leaf.with_df(leaf.df[leaf.df.index.isin(t)])

    return _reindex_leaf(leaf, t, method="ffill")


def _resample_leaf(leaf: Leaf, rule: str) -> Leaf:
    """leaf 하나를 rule 주기로 리샘플링한다(resample_frame()이 각 leaf에
    대해 이 함수를 호출한다)."""
    # {c: leaf.agg.get(c, "last") for c in leaf.value_cols} 은 "딕셔너리
    # 컴프리헨션"이다. value_cols에 있는 컬럼마다 "그 컬럼에 대해 agg에서
    # 지정한 규칙을 쓰되, 지정이 없으면 기본값 'last'를 쓴다"는 딕셔너리를
    # 만든다. 예: {"open": "first", "high": "max", ...} 처럼.
    spec = {c: leaf.agg.get(c, "last") for c in leaf.value_cols}

    if leaf.keys:
        parts = []
        for kv, g in leaf.df.groupby(list(leaf.keys), observed=True, sort=False):
            kv = kv if isinstance(kv, tuple) else (kv,)
            # resample(rule).agg(spec) : rule 주기(예: "1min")로 구간을
            # 나누고, 각 구간 안의 값들을 spec에 정한 방식으로 요약한다.
            # dropna(how="all") : 모든 컬럼이 다 비어있는(그 구간에 원본
            # 데이터가 아예 없던) 행은 결과에서 지운다.
            r = g[leaf.value_cols].resample(rule).agg(spec).dropna(how="all")
            for col, val in zip(leaf.keys, kv):
                r[col] = val
            parts.append(r)
        df = pd.concat(parts).sort_index()
        for k in leaf.keys:
            df[k] = df[k].astype("category")
    else:
        df = leaf.df[leaf.value_cols].resample(rule).agg(spec).dropna(how="all")

    df.index = df.index.rename(TIME)
    return leaf.with_df(df)


def _key_suffix(leaf: Leaf, row_keys) -> str:
    """to_frame()/_pivot_wide()에서 쓰는 컬럼 이름 꼬리표를 만든다.
    예: keys=("symbol",)이고 row_keys="005930"이면 "[005930]"을 돌려준다
    (symbol이 둘 이상이면 "[005930|000660]"처럼 "|"로 이어 붙인다)."""
    if not leaf.keys:
        return ""
    vals = row_keys if isinstance(row_keys, tuple) else (row_keys,)
    return "[" + "|".join(str(v) for v in vals) + "]"


def _pivot_wide(leaf: Leaf) -> pd.DataFrame:
    """키가 있으면 키별로 컬럼을 펼쳐 (t) 유일 인덱스 프레임으로."""
    if not leaf.keys:
        out = leaf.df[leaf.value_cols].copy(deep=False)
        out.columns = [f"{leaf.name}.{c}" for c in out.columns]
        return out

    frames = []
    for kv, g in leaf.df.groupby(list(leaf.keys), observed=True, sort=False):
        sub = g[leaf.value_cols].copy(deep=False)
        sub.columns = [f"{leaf.name}.{c}{_key_suffix(leaf, kv)}" for c in sub.columns]
        frames.append(sub)
    return pd.concat(frames, axis=1)


def to_long(leaf: Leaf, columns: Sequence[str] | None = None,
           dropna: bool = True) -> pd.DataFrame:
    """seaborn 용 롱 포맷. series 컬럼이 hue 가 된다.

    초보자 참고 — "롱(long) 포맷"이 뭔가:
        "넓은(wide) 포맷"은 컬럼이 open/high/low/close처럼 여러 개로
        나뉘어 있는 익숙한 표 모양이다. "롱 포맷"은 그 여러 컬럼을
        "col"(컬럼 이름)과 "value"(그 값) 두 컬럼으로 다시 눕혀서, 표가
        옆으로 넓은 대신 아래로 길어지게 바꾼 모양이다. seaborn 같은
        그래프 라이브러리는 "hue"(색으로 구분할 기준) 하나만 지정하면
        여러 시리즈를 자동으로 색칠해서 겹쳐 그려주는데, 그러려면 데이터가
        이 롱 포맷이어야 한다 — 그래서 그리기 직전에 넓은 표를 롱 포맷
        으로 바꿔주는 게 이 함수의 역할이다.

    columns : (선택) 이 leaf의 value_cols 중 실제로 그릴 컬럼 이름만
              골라서 준다(예: columns=["close"]). None(기본)이면 value_cols
              전부를 그린다 — 지금까지의 동작 그대로다. 여기 적은 이름이
              이 leaf에 없으면 그냥 조용히 무시된다(다른 leaf에는 있을 수
              있으므로 에러를 내지 않는다 — plot_tdata()가 leaf 여러
              개에 같은 columns 목록을 공통으로 적용하기 때문이다).

    dropna : True(기본)면 value가 NaN인 행을 그리기 전에 지운다(지금까지
              동작 그대로). Tdata.time_frame()으로 일부러 빈 구간을
              NaN으로 남겨둔 경우에는 False를 줘야 한다 — 그래야 그
              NaN 행이 그대로 남아서, matplotlib이 그 지점에서 선을
              끊어 그린다(끊긴 구간이 진짜 빈 공간처럼 보인다). 지우면
              양 옆의 점이 그냥 실선으로 이어져서 끊김이 감쪽같이
              사라져 버린다.

    value_name을 바로 "value"로 주면, 원본에 마침 "value"라는 컬럼이
    있을 때(indicator 테이블처럼) pandas가 "value_name이 기존 컬럼과
    겹친다"며 melt 자체를 거부한다(그 컬럼이 melt로 없어질 value_vars
    중 하나여도 마찬가지). 겹칠 수 없는 임시 이름으로 melt한 뒤 되돌려
    붙인다."""
    # columns가 주어졌으면 value_cols 중 그 이름들만 남기고, 없으면
    # (None) 지금까지처럼 value_cols 전부를 그대로 쓴다.
    value_cols = leaf.value_cols if columns is None else [
        c for c in leaf.value_cols if c in columns
    ]
    if not value_cols:
        # columns로 걸렀더니 이 leaf에는 그릴 컬럼이 하나도 안 남았다
        # (예: columns=["close"]인데 이 leaf는 indicator라 "value" 컬럼만
        # 있는 경우). pandas의 melt(value_vars=[])는 빈 결과 대신
        # ValueError("No objects to concatenate")를 던지므로, 여기서
        # 직접 "행이 0개인 빈 롱 프레임"을 만들어 돌려준다 — 호출하는
        # 쪽(plot_tdata)은 "이 leaf는 그릴 게 없다"로 자연스럽게 받아들인다.
        return pd.DataFrame(columns=[TIME, *leaf.keys, "col", "value", "series"])

    df = leaf.df.reset_index()
    id_vars = [TIME, *leaf.keys]
    # DataFrame.melt(...) : 넓은 포맷을 롱 포맷으로 바꿔주는 pandas
    # 함수다. id_vars에 적은 컬럼(t, keys)은 그대로 유지되고, value_vars
    # 에 적은 컬럼들(여기선 value_cols, 즉 숫자 값 컬럼 중 필터링된 것)이
    # 각각 "col"(원래 컬럼 이름)과 "_tdata_value"(그 값) 두 컬럼으로 눕혀진다.
    long = df.melt(id_vars=id_vars, value_vars=value_cols,
                   var_name="col", value_name="_tdata_value")
    # melt가 끝난 뒤에 비로소 "_tdata_value" 컬럼 이름을 우리가 원래
    # 쓰고 싶었던 "value"로 바꾼다(rename) — 이렇게 하면 melt 호출
    # 시점에는 이름이 안 겹치니 에러가 안 나고, 결과 컬럼 이름은 그대로
    # "value"가 되어 아래 코드와 plotter.py가 기대하는 형태를 유지한다.
    long = long.rename(columns={"_tdata_value": "value"})

    # "series"는 이 값이 그래프에서 어떤 선(hue)으로 그려질지 정하는
    # 이름표다. leaf 이름 + 컬럼 이름을 이어 붙이고, keys가 있으면
    # (예: 어떤 종목인지) 그것도 대괄호로 덧붙인다.
    # 예: "bar.close[005930]", "indicator@MACD.value[005930|macd]"
    series = leaf.name + "." + long["col"].astype(str)
    if leaf.keys:
        # DataFrame.agg("|".join, axis=1) : 여러 컬럼(keys)의 값을 한
        # 행마다 "|"로 이어 붙인 문자열 하나로 만든다. axis=1은 "행
        # 방향으로 계산"한다는 뜻(컬럼 방향은 axis=0).
        tag = long[list(leaf.keys)].astype(str).agg("|".join, axis=1)
        series = series + "[" + tag + "]"
    long["series"] = series
    if not dropna:
        # 빈 구간을 일부러 보여주고 싶은 경우(time_frame() 이후) — NaN
        # 행을 그대로 남겨서 matplotlib이 그 지점에서 선을 끊게 둔다.
        return long
    # dropna(subset=["value"]) : value가 비어있는(NaN인) 행은 그래프에
    # 그릴 수 없으니 미리 빼둔다.
    return long.dropna(subset=["value"])
