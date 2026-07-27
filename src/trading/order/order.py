import pandas as pd
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum, auto

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

class OrderStatus(Enum):
    SUBMITTED = auto()
    ACCEPTED = auto()
    REJECTED = auto()
    PARTIAL_FILLED = auto()
    COMPLETED = auto()

@dataclass
class Order:
    order_id: str
    api_value: str | None = None
    symbol: str
    status: str = None
    order_type: str        # limit / market
    qty: float
    price: float | None
    cmpltd_qty = 0
    cmpltd_av_price = 0
    strategy: str | None = None      # ← note 대신 strategy
    note: str | None = None          # note는 strategy와 별개로, 자유롭게 메모할 수 있는 필드
    db_path: str = "kis_data.db"

    def log(self):
        """
        현재 Order 상태를 그대로 sqlite에 append
        """

        row = asdict(self)
        row["logged_at"] = now_iso()

        # db_path 제거 (컬럼으로 안 씀)
        row.pop("db_path")

        df = pd.DataFrame([row])

        with sqlite3.connect(self.db_path) as conn:
            df.to_sql(
                "orders",
                conn,
                if_exists="append",
                index=False
            )

    def update_state(self, status, api_value=None):
        self.status = status

        if api_value is not None:
            self.api_value = api_value
        self.log()