"""analysist 패키지 — 여기서 무엇을 만들 수 있는지 한눈에 모아두는 파일.

■ __init__.py 가 뭐 하는 파일인가 (파이썬 초보자를 위한 설명)
    폴더 안에 "__init__.py"라는 이름의 파일이 있으면, 파이썬은 그 폴더를
    "패키지"로 인식해서 import 할 수 있게 해준다. 그리고 이 파일 안에서
    폴더 속 다른 파일(모듈)의 내용을 미리 꺼내(import) 놓으면, 이 패키지를
    쓰는 쪽에서는 내부 파일 이름을 몰라도 된다.

    예를 들어 이 파일이 없거나 비어 있다면:
        from alpha.analysist.datastore import DataStore   # datastore.py까지 알아야 함

    이 파일에 아래처럼 미리 꺼내두면:
        from alpha.analysist import DataStore              # 훨씬 짧고 편함

■ 이 패키지(analysist 폴더) 안에 있는 파일들
    datastore.py  SQLite 파일(alpha_data.db/mock_data.db/mock_simul.db)에서
                  체결·호가·봉·지표·거래 같은 데이터를 읽어오는 DataStore 클래스.
    tdata.py      읽어온 데이터를 담아두고(Leaf) 여러 개를 겹쳐서 다루는(Tdata)
                  그릇 역할을 하는 클래스들.
    plotter.py    Tdata를 그래프로 그려주는 함수. Tdata.plot()을 부르면
                  내부적으로 이 파일의 함수가 실행된다(직접 부를 일은 거의 없음).
"""

from .datastore import DataStore, DATA_DIR
from .tdata import Leaf, Name, Tdata

# __all__ : "from alpha.analysist import *" 처럼 별표(*)로 한꺼번에
# import할 때 무엇을 내보낼지 정하는 목록이다. 여기 이름을 적어두면
# 사용하는 쪽에서 이 4가지(DataStore, DATA_DIR, Tdata, Leaf, Name)를
# 바로 쓸 수 있다. (plotter.py의 함수처럼 여기 안 적힌 것도, 이름을
# 정확히 지정해서 import하면 당연히 쓸 수 있다 — __all__은 "*" 로
# import할 때만 영향을 준다.)
__all__ = ["DataStore", "DATA_DIR", "Tdata", "Leaf", "Name"]
