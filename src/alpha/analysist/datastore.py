"""
═══════════════════════════════════════════════════════════════════
 datastore.py — alpha_data.db / mock_data.db / mock_simul.db → Tdata/DataFrame
═══════════════════════════════════════════════════════════════════

■ 이 파일이 하는 일
    실전(run_live)/모의(run_sim) 실행 중 Recorder(alpha/recording/
    recorder.py)가 이미 SQLite에 쌓아둔 데이터(체결·거래·지표·봉·틱·
    호가·체결통보)를, 사후 분석하거나 backtrader에 되돌려 넣을 때
    이 서비스를 거쳐서 받는다.

    한 문장으로: "SQLite 파일 안의 표(테이블) 하나를 파이썬에서 다루기
    좋은 형태(DataFrame 또는 Tdata)로 꺼내오는 창구"가 이 파일이다.

■ 반환 타입 — load() 는 DataFrame, 나머지 전용 메서드는 Tdata
    load(table, ...) 는 table 이 실행 시점에 정해지는 문자열이라 이름/
    엔티티 축을 미리 알 수 없으므로 그냥 pandas DataFrame을 돌려준다.
    반면 ticks()/quotes()/bars()/fills()/notices()/indicators()/
    trades()/strategies() 는 "이 테이블은 무엇으로 식별되는가"를 이미
    아는 전용 메서드이므로, tdata.Tdata(analysist/tdata.py)로 감싸서
    돌려준다 — plot()/time_sync()/resample_frame()/time_frame()/
    split_by_gap() 등을 바로 쓸 수 있고, 여러 개를 Tdata.add()/`|`로
    겹쳐 붙여 함께 그릴 수 있다.
    원본 DataFrame이 필요하면 .df 로 꺼낸다(예: store.ticks("005930").df).

    label/seconds/strategy_id 처럼 "이 테이블 안의 하위 종류"를 고르는
    필터를 주면 Tdata 이름이 "table@값"으로 접힌다(indicators(label=
    "MACD") → "indicator@MACD"). 안 주면 그 컬럼이 아직 여러 값을 가질
    수 있으므로 Tdata.keys 에 남겨서, 필요할 때 심볼처럼 나눠 그릴 수
    있게 해둔다.

■ 공식 DB 세 개만 다룬다 — "실행 경로가 곧 데이터 성격"
    data/alpha_data.db   실전(run_live) — --real 여부와 무관하게 항상 이 파일
    data/mock_data.db    모의(run_sim), --simul 없이 — 실제 KIS 웹소켓
                          시세 + 가짜 주문
    data/mock_simul.db   모의(run_sim), --simul — fake_kis_websocket
                          (장 마감 후·주말 테스트용) + 가짜 주문
    (data/kis_data*.db 는 KiSEngine이 쌓는 원본 시세용 — 이 파일은
     그쪽을 다루지 않는다. 필요해지면 같은 자리에 별도 로더를 둘 것.)

■ 테이블 = __main__.py의 build_trader()가 add_recording()으로 등록한 것
    tick / quote / bar / notice / fill / trade / indicator
    각 테이블의 컬럼은 그 타입의 dataclass 필드 그대로다(alpha.events.
    events / alpha.trader.trading 참고) — 여기서 컬럼을 새로 정의하지
    않는다. 스키마가 바뀌면(필드 추가 등) 이 파일을 안 고쳐도 그대로
    따라온다 — 시간/JSON 컬럼 이름표만 알고 있으면 된다.

■ 시간 컬럼
    저장할 때(SqliteSink._flat, alpha/recording/sinks.py) datetime은
    ISO 문자열로 눌려 들어간다. 불러올 때 그 컬럼(들)을 다시 datetime
    으로 복원하고, 기본으로 그 컬럼을 DatetimeIndex로 세워 오름차순
    정렬까지 한다 — bt.feeds.PandasData가 그대로 요구하는 형태다.
    trade만 시작(entry_dt)/끝(exit_dt) 두 개를 가져서, 인덱스는
    entry_dt를 쓴다(라운드트립이라 봉 인덱스 하나로는 안 잡힌다).

■ kis_config 를 안 쓰는 이유
    kis_config 모듈은 import 되는 순간 KIS 토큰 갱신 네트워크 호출이
    걸린다(kis_tocken.get_or_refresh_token()). 로컬 SQLite만 읽는 이
    파일이 그 부작용을 물려받을 이유가 없어서, data 폴더 경로를 직접
    계산한다(값 자체는 kis_config.DATA_DIR 와 같다).

■ 앞으로 확장
    kis_api REST로 과거 시세 등을 받아오는 소스는 이 옆에 별도 모듈로
    추가할 예정이다(예: alpha/analysist/kis_source.py). symbol/start/end
    필터 관례를 이 파일과 맞추면 두 소스를 같은 방식으로 섞어 쓸 수
    있다.
"""

# 파이썬 초보자를 위한 참고: 아래 import들은 이 파일 어디선가 쓰는
# "도구 상자"를 미리 가져오는 부분이다. 하나씩 무엇에 쓰는지 적어둔다.
from __future__ import annotations
# ↑ 이 한 줄은 "타입 힌트(자료형 표시)를 실제로 계산하지 않고 글자
#   그대로만 남겨둔다"는 파이썬의 설정이다. 예를 들어 def f() -> "Foo":
#   처럼 따옴표를 안 붙여도 되게 해준다. 실행 결과에는 영향이 없고,
#   타입 힌트를 조금 더 자유롭게 쓸 수 있게 해주는 문법적 편의다.

import json      # 문자열로 저장된 리스트(JSON)를 다시 파이썬 리스트로 되돌릴 때 씀
import sqlite3   # 파이썬 표준 라이브러리 — SQLite 데이터베이스 파일에 접속하는 도구
from pathlib import Path            # 파일 경로를 다루는 표준 도구 (문자열보다 안전/편리)
from typing import Optional, Sequence, Union
# ↑ 타입 힌트 전용 도구들.
#   Optional[X]      = X 이거나 None 이어도 된다는 뜻 (Union[X, None]의 줄임말)
#   Sequence[X]      = 리스트/튜플처럼 "순서가 있고 반복 가능한" X들의 모음
#   Union[A, B, ...] = A 이거나 B 이거나 ... 중 하나면 된다는 뜻

import pandas as pd   # 표(테이블) 형태 데이터를 다루는 라이브러리. DataFrame이 핵심 자료형이다.

from .tdata import Tdata
# ↑ 같은 폴더(analysist) 안의 tdata.py에서 Tdata 클래스를 가져온다.
#   앞에 점(.)을 붙인 건 "같은 패키지 안의 상대 경로"라는 뜻이다.

# 프로젝트 루트/data. kis_config.DATA_DIR 와 같은 값이지만, 위 docstring의
# 이유로 kis_config 를 import 하지 않고 직접 계산한다.
#   이 파일: <root>/src/alpha/analysist/datastore.py
#   parents[3] == <root>  (analysist → alpha → src → <root>)
#
# 초보자 참고: __file__ 은 "지금 실행 중인 이 파이썬 파일 자신의 경로"를
# 담고 있는 특별한 변수다. .resolve() 는 상대경로를 절대경로로 바꿔주고,
# .parents[n] 은 그 경로에서 n번째 위 폴더를 가리킨다(0이면 바로 위 폴더).
DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# symbol 인자로 문자열 하나("005930")를 줘도 되고, 여러 개(["005930",
# "000660"])를 줘도 되고, 아예 안 줘도(None, 전체 종목) 된다는 뜻의
# 타입 별명(alias)이다. 매번 Union[...]을 풀어 쓰지 않으려고 이름을
# 붙여둔 것 — 아래 메서드들의 symbol 인자 타입 힌트에서 계속 쓰인다.
Symbols = Union[str, Sequence[str], None]

# 테이블 이름 -> 시간 컬럼(들). 맨 앞이 인덱스로 세울 기본 컬럼이자
# start/end 필터의 기준 컬럼이다. trade만 둘이다(entry_dt/exit_dt).
#
# 초보자 참고: 이런 걸 "딕셔너리(dict)"라고 한다. {키: 값, 키: 값, ...}
# 형태로, 키("tick")를 주면 값(("dt",))을 바로 찾아 쓸 수 있는 표다.
# 값이 ("dt",)처럼 괄호+쉼표인 건 "튜플(tuple)"이라는 자료형이다 —
# 리스트([1,2,3])와 비슷하지만 한 번 만들면 내용을 못 바꾼다(불변).
# 원소가 하나뿐이어도 쉼표를 꼭 붙여야 튜플로 인식된다 — ("dt") 는
# 그냥 괄호 친 문자열 "dt"일 뿐이고, ("dt",) 가 "dt" 하나짜리 튜플이다.
_TIME_COLUMNS: dict[str, tuple[str, ...]] = {
    "tick": ("dt",),
    "quote": ("dt",),
    "bar": ("dt",),
    "notice": ("dt",),
    "fill": ("dt",),
    "indicator": ("dt",),
    "trade": ("entry_dt", "exit_dt"),
    "strategy": ("dt",),
}

# 저장할 때 list/tuple이라 JSON 문자열로 눌린 컬럼. 불러올 때 다시 판다.
# (SQLite는 리스트/튜플 같은 복합 자료형을 그대로 저장하지 못해서, 저장할
#  당시에 JSON 문자열, 예: "[1, 2, 3]" 로 바꿔서 넣어뒀다. 여기서는 그
#  반대로, 문자열을 다시 파이썬 리스트로 파싱해서 돌려준다.)
_JSON_COLUMNS: dict[str, tuple[str, ...]] = {
    "quote": ("asks", "bids", "ask_sizes", "bid_sizes"),
    "trade": ("fills",),
    "strategy": ("ticks", "quotes", "bars"),
}

# 저장할 때 bool이 sqlite INTEGER(0/1)로 떨어지는 컬럼. 불러올 때 bool로 되돌린다.
# (SQLite에는 참/거짓(bool) 전용 타입이 없어서 0과 1인 정수로 저장된다.
#  그걸 다시 True/False로 되돌리는 대상 컬럼 목록이다.)
_BOOL_COLUMNS: dict[str, tuple[str, ...]] = {
    "notice": ("rejected",),
}


class DataStore:
    """alpha_data.db(실전) / mock_data.db(모의, 실제 시세) /
    mock_simul.db(모의, fake 피드)에서 테이블을 읽는다.

    live/simul_mode 조합은 alphatrader.py의 run_live/run_sim 이 정하는
    파일 선택과 정확히 같다:

        live=True                        → alpha_data.db   (run_live)
        live=False, simul_mode=False     → mock_data.db     (run_sim, --simul 없이)
        live=False, simul_mode=True      → mock_simul.db    (run_sim --simul)

    사용 예:
        store = DataStore(live=False)               # mock_data.db
        store = DataStore(live=False, simul_mode=True)  # mock_simul.db
        df = store.load("bar", symbol="005930")     # 테이블 하나, 조건 걸어서 → DataFrame
        bars = store.bars("005930", seconds=60)     # → Tdata (bars.df 로 원본도 꺼낼 수 있음)
        ohlcv = store.ohlcv("005930", seconds=60)   # backtrader에 바로 넣을 형태(DataFrame)
        macd = store.indicator_pivot("MACD", "005930")  # 라인별 컬럼으로 편 지표(DataFrame)
    """

    # 클래스 변수 — 인스턴스(store = DataStore(...))를 안 만들어도
    # DataStore.TABLES 로 바로 접근할 수 있는, 모든 인스턴스가 공유하는 값이다.
    TABLES = ("tick", "quote", "bar", "notice", "fill", "trade", "indicator", "strategy")

    def __init__(self, live: bool = True, simul_mode: bool = False,
                 path: Optional[Union[str, Path]] = None):
        """live=True 면 alpha_data.db(simul_mode은 이때 안 본다).
        live=False 면 simul_mode 로 mock_data.db/mock_simul.db 를 고른다.
        path 를 직접 주면 live/simul_mode 는 전부 무시하고 그 파일을
        연다(테스트용, 혹은 예전 alpha_data_sim.db 같은 파일을 그대로
        읽고 싶을 때)."""
        # __init__ 은 "생성자"라고 부르는 특별한 메서드다. DataStore(...)
        # 처럼 클래스 이름을 함수처럼 호출하면, 파이썬이 자동으로 이
        # __init__ 을 실행해서 인스턴스(store 객체)를 준비해준다.
        # self 는 "지금 만들어지고 있는 그 인스턴스 자신"을 가리킨다.
        if path is not None:
            self.path = Path(path)
        elif live:
            self.path = DATA_DIR / "alpha_data.db"
        else:
            # 파이썬의 "삼항 조건식(조건부 표현식)" 문법이다.
            # "A if 조건 else B" 는 "조건이 참이면 A, 아니면 B" 라는 뜻.
            self.path = DATA_DIR / ("mock_simul.db" if simul_mode else "mock_data.db")
        self.live = live
        self.simul_mode = simul_mode

    # ── 연결 ─────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        """읽기 전용으로만 연다.

        ★ 왜 읽기 전용인가 ★
          live/sim 프로세스가 지금 이 순간에도 같은 파일에 쓰고 있을 수
          있다(WAL 모드라 동시 읽기는 안전하다). 여기서는 절대 쓰지
          않을 것이므로 mode=ro 로 명시해, 실수로 쓰기가 나가는 것도
          막고 '분석 코드가 원본 데이터를 훼손했다' 는 사고 가능성 자체를
          없앤다."""
        # 초보자 참고: 이름이 밑줄(_)로 시작하는 메서드/함수(_connect,
        # _build_query 등)는 "이 클래스/파일 내부에서만 쓰려고 만든
        # 것"이라는 파이썬의 관례다. 강제로 막혀 있진 않지만, 바깥에서
        # 부르지 말라는 신호로 보면 된다.
        if not self.path.exists():
            raise FileNotFoundError(
                f"DB 파일이 없습니다: {self.path} "
                "(run_live/run_sim을 한 번도 안 돌렸거나, live/simul_mode 조합을 "
                "반대로 골랐을 수 있습니다 — live=True→alpha_data.db, "
                "live=False,simul_mode=False→mock_data.db, "
                "live=False,simul_mode=True→mock_simul.db)")
        # sqlite3.connect() 에 "file:...?mode=ro" 같은 URI 형태 문자열을
        # 주고 uri=True 를 켜면, 그 파일을 "읽기 전용"으로만 연다 —
        # 이 연결로는 INSERT/UPDATE 같은 쓰기 명령을 실행해도 에러가 난다.
        uri = f"file:{self.path.resolve().as_posix()}?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def tables(self) -> list[str]:
        """이 DB 파일에 실제로 존재하는 테이블 이름.

        TABLES 상수와 다를 수 있다 — add_recording() 은 등록만 해두고,
        Recorder 는 실제로 그 타입이 한 번 이상 put() 돼야 배치를
        flush 해서 테이블을 만든다(SqliteSink.write() 가 최초 호출 때
        to_sql 로 생성). 즉 한 번도 안 들어온 타입은 테이블 자체가 없다."""
        # "with ... as conn:" 은 "이 블록이 끝나면 conn을 자동으로 뒷정리
        # (여기서는 연결 닫기)해준다"는 파이썬 문법이다(컨텍스트 매니저).
        # 직접 conn.close()를 안 써도 되게 해줘서, 닫는 걸 깜빡할 걱정이 없다.
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        # 이 아래는 "리스트 컴프리헨션"이다 — "for r in rows" 로 하나씩
        # 꺼내면서 "r[0]" (테이블 이름만) 을 모아 새 리스트를 만든다.
        # for 반복문 + append() 를 한 줄로 줄여 쓴 것과 같다.
        return sorted(r[0] for r in rows)

    # ── 핵심 ─────────────────────────────────────────────────
    def load(self, table: str, symbol: Symbols = None,
             start=None, end=None, where: Optional[str] = None,
             params: Sequence = (), index: bool = True,
             parse_json: bool = True) -> pd.DataFrame:
        """테이블 하나를 DataFrame으로.

        symbol   문자열 하나 또는 목록. 없으면 전 종목.
        start/end  기본 시간 컬럼(trade 는 entry_dt) 기준 필터.
                   "2024-01-15"/"2024-01-15 09:00:00" 같은 문자열이든
                   date/datetime 객체든 그대로 받는다.
        where/params  더 세밀한 조건이 필요하면 원본 SQL WHERE 조각과
                   그 자리표시자(?) 값을 직접 준다. symbol/start/end와
                   AND로 묶인다.
                       store.load("fill", where="price > ?", params=[70000])
        index    True(기본)면 시간 컬럼을 DatetimeIndex로 세우고 오름차순
                 정렬한다. False면 dt를 평범한 컬럼으로 남긴다
                 (groupby 등에서 컬럼으로 쓰고 싶을 때).
        parse_json  True(기본)면 리스트/튜플이었던 컬럼(quote의 asks 등)을
                    다시 파이썬 리스트로 풀어준다.

        ★ where/params 에 왜 "?"를 쓰는가(SQL 인젝션 방지) ★
          "price > 70000" 처럼 값을 문자열에 직접 끼워 넣지 않고
          "price > ?" + params=[70000] 형태로 분리하는 걸 "매개변수화
          쿼리(파라미터 바인딩)"라고 부른다. sqlite3가 값을 안전하게
          채워 넣어주므로, 값 안에 따옴표나 이상한 문자가 섞여도 SQL
          구문이 깨지거나 악용될 걱정이 없다."""
        if table not in _TIME_COLUMNS:
            raise ValueError(f"모르는 테이블: {table!r} (알려진 것: {', '.join(self.TABLES)})")

        sql, sql_params = self._build_query(table, symbol, start, end, where, params)
        with self._connect() as conn:
            # pandas의 read_sql_query 는 "SQL을 실행하고, 결과를 바로
            # DataFrame(표)으로 만들어주는" 함수다. sqlite3로 커서를 열고
            # fetchall() 해서 직접 DataFrame을 만드는 수고를 덜어준다.
            df = pd.read_sql_query(sql, conn, params=sql_params)

        df = self._parse_datetimes(df, table)
        if parse_json:
            df = self._parse_json_columns(df, table)
        df = self._parse_bools(df, table)
        if index:
            df = self._set_index(df, table)
        return df

    # ── SQL 조립 ─────────────────────────────────────────────
    def _build_query(self, table, symbol, start, end, where, params):
        """symbol/start/end/where 조건들을 모아 실제 SQL 문자열 하나로
        조립한다. load() 안에서만 쓰이는 내부 헬퍼(도우미) 함수다."""
        time_col = _TIME_COLUMNS[table][0]
        clauses: list[str] = []   # "WHERE symbol = ?" 같은 조건 조각들을 모아둘 리스트
        values: list = []         # 그 조건들의 "?" 자리에 채울 실제 값들을 순서대로 모아둘 리스트

        if symbol is not None:
            if isinstance(symbol, str):
                # symbol="005930" 처럼 문자열 하나만 왔으면, 아래 로직이
                # "여러 개짜리 리스트"를 가정하고 동작하므로 리스트로 감싸준다.
                symbol = [symbol]
            # symbol이 ["005930", "000660"] 이면 "?,?" 처럼 종목 개수만큼
            # 물음표를 콤마로 이어 붙인다 — SQL의 "IN (?,?)" 구문에 쓸 자리다.
            placeholders = ",".join("?" for _ in symbol)
            clauses.append(f"symbol IN ({placeholders})")
            # values.extend(symbol) : values 리스트 끝에 symbol의 원소들을
            # 하나씩 풀어서 이어 붙인다(append와 달리 리스트 자체를
            # 통째로 넣는 게 아니라, 그 안의 값들을 낱개로 추가한다).
            values.extend(symbol)

        if start is not None:
            clauses.append(f"{time_col} >= ?")
            values.append(_to_iso(start))
        if end is not None:
            clauses.append(f"{time_col} <= ?")
            values.append(_to_iso(end))

        if where:
            # 사용자가 직접 준 조건은 괄호로 한 번 감싸서 다른 AND 조건들과
            # 섞였을 때 우선순위(연산 순서)가 헷갈리지 않게 한다.
            clauses.append(f"({where})")
            values.extend(params)

        sql = f"SELECT * FROM {table}"
        if clauses:
            # 조건 조각들을 전부 " AND "로 이어 붙여 하나의 WHERE 절로 만든다.
            # 예: ["symbol IN (?)", "dt >= ?"] → "symbol IN (?) AND dt >= ?"
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {time_col}"
        return sql, values

    # ── 후처리 ────────────────────────────────────────────────
    # 아래 세 메서드(_parse_datetimes/_parse_json_columns/_parse_bools)는
    # SQLite에서 막 읽어온 "날것의" DataFrame을, 원래 파이썬에서 쓰던
    # 자료형(날짜/시간, 리스트, 참거짓)으로 되돌려주는 후처리 단계다.
    # SQLite는 이런 타입들을 그대로 저장 못 해서(문자열/숫자로만 저장),
    # 불러올 때마다 매번 원래 형태로 복원해줘야 한다.
    def _parse_datetimes(self, df: pd.DataFrame, table: str) -> pd.DataFrame:
        for col in _TIME_COLUMNS.get(table, ()):
            if col in df.columns:
                # pd.to_datetime : 문자열 컬럼("2024-01-15T09:30:00" 등)을
                # 진짜 날짜/시간 자료형(datetime64)으로 바꿔준다.
                # errors="coerce" : 이상한 값이 섞여 있으면 에러를 내는 대신
                # 그 칸만 NaT(비어있는 날짜)로 처리하고 나머지는 계속 진행한다.
                df[col] = pd.to_datetime(df[col], errors="coerce")
        # recv_dt(로컬 수신 시각, 마이크로초 있음)는 tick/quote/notice에만
        # 있고 인덱스로 쓰지는 않지만(기준 시계는 여전히 dt), 있으면
        # datetime dtype으로는 맞춰준다 — 틱 간격 분석에 바로 쓰려면
        # 문자열로 남아 있으면 안 된다.
        if "recv_dt" in df.columns:
            df["recv_dt"] = pd.to_datetime(df["recv_dt"], errors="coerce")
        return df

    def _parse_json_columns(self, df: pd.DataFrame, table: str) -> pd.DataFrame:
        for col in _JSON_COLUMNS.get(table, ()):
            if col in df.columns:
                # Series.map(함수) : 그 컬럼의 각 칸(값)마다 함수를 한 번씩
                # 적용해서, 그 결과들로 새 컬럼을 만든다. 여기서는 각 칸의
                # JSON 문자열을 _safe_json_loads로 파이썬 리스트로 바꾼다.
                df[col] = df[col].map(_safe_json_loads)
        return df

    def _parse_bools(self, df: pd.DataFrame, table: str) -> pd.DataFrame:
        for col in _BOOL_COLUMNS.get(table, ()):
            if col in df.columns:
                df[col] = df[col].astype("boolean")   # nullable bool — 결측도 받는다
        return df

    def _set_index(self, df: pd.DataFrame, table: str) -> pd.DataFrame:
        """시간 컬럼을 DataFrame의 "인덱스"로 세운다.

        초보자 참고: pandas DataFrame은 평범한 컬럼들 말고 "인덱스"라는
        특별한 라벨 축을 하나 더 갖는다(기본은 0,1,2... 번호). 시간
        컬럼을 인덱스로 세워두면 df.loc["2024-01-15":"2024-01-16"] 처럼
        날짜로 바로 슬라이싱할 수 있고, backtrader 같은 라이브러리도
        이 형태(DatetimeIndex)를 기대한다."""
        idx_col = _TIME_COLUMNS[table][0]
        if idx_col not in df.columns:
            return df
        return df.sort_values(idx_col).set_index(idx_col)

    # ── Tdata 포장 ───────────────────────────────────────────
    def _tdata(self, table: str, df: pd.DataFrame, label=None,
              keys: tuple[str, ...] = ()) -> Tdata:
        """table(@label) 을 Tdata 이름으로, keys 중 실제 있는 컬럼만 골라 감싼다.

        label 을 주는 쪽(indicators(label=...), bars(seconds=...) 등)은
        그 값 하나로 고정됐으므로 keys 에서 뺀다 — 이미 상수라 나눠 그릴
        의미가 없다. 안 주면(여러 값이 섞여 있을 수 있으면) keys 에 남겨서
        symbol 처럼 Tdata 가 엔티티별로 나눠 다룰 수 있게 한다."""
        # f-string(f"...") 안에 {label}처럼 변수를 넣으면 그 값이 문자열
        # 안에 그대로 끼워진다. 예: table="indicator", label="MACD" 이면
        # name은 "indicator@MACD" 가 된다.
        name = f"{table}@{label}" if label is not None else table
        keys = tuple(k for k in keys if k in df.columns)
        return Tdata.from_df(name, df, keys=keys)

    # ── 자주 쓰는 조합 ────────────────────────────────────────
    # 아래부터는 load()를 매번 인자 다 채워서 부르지 않아도 되게 만든
    # "자주 쓰는 조합"들이다. 전부 내부적으로는 load()를 부르고, 그
    # 결과를 Tdata로 감싸서 돌려준다(ohlcv/indicator_pivot 제외 — 이
    # 둘은 특수 목적이라 그냥 DataFrame을 돌려준다, 아래 설명 참고).
    def ticks(self, symbol: Symbols = None, start=None, end=None) -> Tdata:
        """체결(tick) 데이터. symbol 하나 이상 줄 수 있다."""
        df = self.load("tick", symbol=symbol, start=start, end=end)
        return self._tdata("tick", df, keys=("symbol",))

    def quotes(self, symbol: Symbols = None, start=None, end=None) -> Tdata:
        """호가(quote) 데이터."""
        df = self.load("quote", symbol=symbol, start=start, end=end)
        return self._tdata("quote", df, keys=("symbol",))

    def bars(self, symbol: Symbols = None, seconds: Optional[int] = None,
             start=None, end=None) -> Tdata:
        """봉(bar) 데이터. seconds(봉 주기, 예: 60=1분봉)를 주면 그 주기만
        걸러서 SQL 단계에서 가져온다(전부 읽어서 나중에 거르지 않는다 —
        _eq_clause 설명 참고)."""
        where, params = _eq_clause("seconds", seconds)
        df = self.load("bar", symbol=symbol, start=start, end=end,
                       where=where, params=params)
        keys = ("symbol",) if seconds is not None else ("symbol", "seconds")
        return self._tdata("bar", df, label=seconds, keys=keys)

    def ohlcv(self, symbol: str, seconds: int = 60, start=None, end=None) -> pd.DataFrame:
        """backtrader.feeds.PandasData(dataname=...) 에 바로 넣을 수 있는
        형태 — DatetimeIndex + open/high/low/close/volume 만 남긴다(Tdata가
        아니라 그냥 DataFrame — backtrader는 이 형태를 그대로 요구한다).

            import backtrader as bt
            df = store.ohlcv("005930", seconds=60)
            cerebro.adddata(bt.feeds.PandasData(dataname=df))
        """
        # bars()가 이제 Tdata를 돌려주므로, 원본 DataFrame이 필요하면
        # 뒤에 ".df"를 붙여서 꺼낸다.
        df = self.bars(symbol=symbol, seconds=seconds, start=start, end=end).df
        # 리스트 컴프리헨션: 다섯 컬럼 이름 중 실제로 df에 있는 것만 골라낸다
        # (혹시 volume 컬럼이 없는 데이터라도 에러 없이 있는 것만 남긴다).
        cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        return df[cols]

    def trades(self, symbol: Symbols = None, strategy_id: Optional[str] = None,
               start=None, end=None) -> Tdata:
        """완결된 거래(trade, 진입~청산 한 쌍) 기록."""
        where, params = _eq_clause("strategy_id", strategy_id)
        df = self.load("trade", symbol=symbol, start=start, end=end,
                       where=where, params=params)
        keys = ("symbol",) if strategy_id is not None else ("symbol", "strategy_id")
        return self._tdata("trade", df, label=strategy_id, keys=keys)

    def fills(self, symbol: Symbols = None, start=None, end=None) -> Tdata:
        """체결통보(fill, 주문이 실제로 얼마에 얼마나 체결됐는지) 기록."""
        df = self.load("fill", symbol=symbol, start=start, end=end)
        return self._tdata("fill", df, keys=("symbol",))

    def notices(self, symbol: Symbols = None, start=None, end=None) -> Tdata:
        """체결통보 외 일반 알림(notice, 주문 거부 등) 기록."""
        df = self.load("notice", symbol=symbol, start=start, end=end)
        return self._tdata("notice", df, keys=("symbol",))

    def indicators(self, symbol: Symbols = None, label: Optional[str] = None,
                   line: Optional[str] = None, start=None, end=None) -> Tdata:
        """지표(indicator) 값. label은 지표 이름(예: "MACD"), line은 그
        지표 안의 세부 선(예: MACD의 "macd"/"signal"/"histo") 이름이다.
        둘 다 SQL WHERE 절에서 바로 걸러서 가져온다(_and_clauses 설명
        참고) — indicator 테이블은 수천만~수억 행까지 쌓일 수 있어서,
        일단 다 읽고 나서 파이썬에서 거르면 매우 느리다."""
        where, params = _and_clauses(("label", label), ("line", line))
        df = self.load("indicator", symbol=symbol, start=start, end=end,
                       where=where, params=params)
        keys = ["symbol"]
        if label is None:
            keys.append("label")
        if line is None:
            keys.append("line")
        return self._tdata("indicator", df, label=label, keys=tuple(keys))

    def indicator_pivot(self, label: str, symbol: str,
                        start=None, end=None) -> pd.DataFrame:
        """지표 하나를 라인별 컬럼으로 편다 — MACD처럼 라인이 여러 개인
        지표를 macd/signal/histo 컬럼으로 나란히 보고 싶을 때 쓴다
        (indicators.py 의 다중 라인 설계, __main__.py 의 where={"label":
        "MACD"} 등록과 짝이 맞는다).

            store.indicator_pivot("MACD", "005930")
                → index=dt, columns=["macd","signal","histo"]

        라인이 하나뿐인 지표(SMA 등)에 써도 동작한다 — 컬럼이 하나(값
        하나)로 나올 뿐이다. Tdata가 아니라 pivot된 DataFrame을 그대로
        반환한다 — 이미 "라인별 컬럼"으로 편 결과라 Tdata의 symbol/line
        keys 개념과 맞지 않는다."""
        df = self.indicators(symbol=symbol, label=label, start=start, end=end).df
        if df.empty:
            return df
        # pivot_table : "긴(long) 형태" 표(한 줄에 값 하나씩)를 "넓은(wide)
        # 형태" 표(라인 이름이 컬럼이 되고, 그 아래 값들이 채워짐)로
        # 바꿔주는 pandas 기능이다. index=df.index(시간)를 그대로 두고,
        # columns="line"(라인 이름들이 새 컬럼이 됨), values="value"
        # (그 칸을 채울 값), aggfunc="last"(같은 시간에 값이 여럿이면
        # 마지막 값만 쓴다)로 재구성한다.
        return df.pivot_table(index=df.index, columns="line", values="value", aggfunc="last")

    def strategies(self, strategy_id: Optional[str] = None,
                   start=None, end=None) -> Tdata:
        """전략이 실행마다 등록해둔 구독 스펙(ticks/quotes/bars) 이력.
        strategy_id 는 재실행해도 이름이 같으므로, 같은 strategy_id 에
        대해 실행 시각(dt)마다 한 행씩 쌓여 있다 — "그때 설정"을 보려면
        spec_for() 를 쓴다."""
        where, params = _eq_clause("strategy_id", strategy_id)
        df = self.load("strategy", start=start, end=end, where=where, params=params)
        keys = () if strategy_id is not None else ("strategy_id",)
        return self._tdata("strategy", df, label=strategy_id, keys=keys)

    def spec_for(self, strategy_id: str, at) -> Optional[dict]:
        """Trade.entry_dt 시점에 그 전략에 실제로 활성이었던 구독 스펙.

        전략은 재실행할 때마다(코드가 바뀌었을 수도 있으므로) 새 행을
        남긴다 — 그래서 "지금 설정"이 아니라 at 이전 중 가장 최근 행을
        찾아야 그 Trade 가 실제로 어떤 bars/ticks/quotes 를 보고 나온
        것인지 안다.

            trade = store.trades(strategy_id="추세").df.iloc[0]
            spec = store.spec_for("추세", trade.name)   # trade.name == entry_dt(인덱스)
            spec["bars"]   # [["005930", 60]] 처럼 리스트로 이미 풀려 있다

        해당 시각 이전에 등록 기록이 없으면 None(오래된 데이터거나,
        strategy 테이블을 이번에 추가하기 전 실행일 수 있다)."""
        df = self.strategies(strategy_id=strategy_id, end=at).df
        if df.empty:
            return None
        # df.iloc[-1] : 행 번호(위치) 기준으로 "맨 마지막 행"을 꺼낸다.
        # end=at 으로 이미 "at 이전"까지만 걸러서 시간순 정렬해뒀으므로,
        # 맨 마지막 행이 곧 "at 시점에서 가장 최근" 행이 된다.
        # .to_dict() : 그 한 행(Series)을 {컬럼명: 값} 딕셔너리로 바꾼다.
        return df.iloc[-1].to_dict()


# ── 유틸 ─────────────────────────────────────────────────────
# 여기부터는 클래스 밖에 있는 "그냥 함수"들이다. self를 안 받는 걸 보면
# 알 수 있다 — 특정 DataStore 인스턴스에 속하지 않고 독립적으로 동작하는
# 계산만 담당한다(그래서 클래스 메서드로 안 두고 모듈 함수로 뺐다).
def _eq_clause(column: str, value) -> tuple[Optional[str], list]:
    """value 가 None 이면 필터 없음(전체), 아니면 "column = ?" 한 조각.

    label/line/strategy_id/seconds 같은 부가 필터를 SQL WHERE 로 밀어
    넣기 위한 최소 단위 — indicator 처럼 1억 행을 넘길 수 있는 테이블에서
    "일단 symbol/기간으로 다 읽고 판다스에서 label==... 로 거른다"는
    방식은 디스크에서 안 쓸 행까지 전부 읽어오므로 느리다. load() 가
    이미 지원하는 where=/params= 를 그대로 타면, 인덱스가 있을 때
    SQLite 가 필요한 행만 골라 읽는다(sinks.py 의 _INDEX_HINTS 참고)."""
    if value is None:
        return None, []
    return f"{column} = ?", [value]


def _and_clauses(*pairs: tuple[str, Optional[object]]) -> tuple[Optional[str], list]:
    """(컬럼, 값) 쌍 여러 개를 AND 로 묶는다 — 값이 None 인 쌍은 건너뛴다.
    indicators() 처럼 label/line 을 동시에 필터할 때 쓴다.

    초보자 참고: 함수 정의에서 인자 앞에 별표 하나(*pairs)를 붙이면,
    "이 자리에 몇 개를 넘기든 전부 하나의 튜플로 묶어서 받는다"는 뜻이다.
    예: _and_clauses(("label","MACD"), ("line","macd")) 를 부르면
    pairs는 (("label","MACD"), ("line","macd")) 라는 튜플이 된다."""
    clauses: list[str] = []
    values: list = []
    for column, value in pairs:
        if value is None:
            continue   # 값이 없는 필터는 조건에서 아예 빼고 다음 쌍으로 넘어간다
        clauses.append(f"{column} = ?")
        values.append(value)
    if not clauses:
        return None, []
    return " AND ".join(clauses), values


def _to_iso(value) -> str:
    """start/end 로 받은 값(문자열/date/datetime)을 SQL 비교용 문자열로.

    문자열이면 그대로 믿는다 — SqliteSink 가 저장한 ISO 문자열
    ("2024-01-15T09:30:00")과 사전순 비교가 곧 시간순 비교이므로,
    "2024-01-15"처럼 앞부분만 줘도 그날 00:00:00 이후로 정확히 걸린다."""
    if isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        # hasattr(값, "isoformat") : 그 값이 isoformat이라는 기능(메서드)을
        # 가지고 있는지 확인한다. datetime/date 객체들은 이 기능으로
        # 자기 자신을 "2024-01-15T09:30:00" 같은 표준 문자열로 바꿔준다.
        return value.isoformat()
    raise TypeError(f"start/end 는 문자열이나 date/datetime 이어야 합니다: {value!r}")


def _safe_json_loads(value):
    """JSON 파싱 실패·NULL 은 원본 그대로 돌려준다 — 행 하나가 이상하다고
    분석 전체가 죽는 것보다, 그 칸만 문자열로 남는 게 낫다(다른 행은
    그대로 쓸 수 있다)."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        # try/except : "이 코드를 실행해보고, 특정 종류의 에러가 나면
        # 프로그램을 멈추지 말고 여기서 대신 처리해라"는 문법이다.
        # 여기서는 JSON으로 못 읽는 값이면 그냥 원래 문자열을 돌려준다.
        return value
