from kis import kis_engine
from kis import kis_logger
from kis import kis_config
from trading.config import log
from trading.strategy import test_st

if __name__ == "__main__":

    logger = kis_logger.setup_logger(kis_config.LOG_DIR)
    logger = log.setup_logger(kis_config.LOG_DIR)
    logger.info("kis_engine start")
    
    # 예시: 삼성전자(005930) 시세 + 체결 통보 구독
    kospi_codes = [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "035420",  # NAVER
        "035720",  # 카카오
        "005380",  # 현대차
        "000270",  # 기아
        "012330",  # 현대모비스
        "068270",  # 셀트리온
        "105560",  # KB금융
        "055550",  # 신한지주
        "086790",  # 하나금융지주
        "316140",  # 우리금융지주
        "005490",  # POSCO홀딩스
        "051910",  # LG화학
        "006400",  # 삼성SDI
        "003550",  # LG
        "066570",  # LG전자
        "034730",  # SK
    ]

    # price_codes = ["000660", "005930"]
    # orderbook_codes = ["000660", "005930"]
    price_codes = kospi_codes
    orderbook_codes = kospi_codes

    # simul_engine = kis_engine.KiSEngine(price_codes=price_codes, orderbook_codes=orderbook_codes, simul_mode=True)
    # simul_engine.add_strategy(test_st.KisOvernightMomentumStrategy)
    # simul_engine.run(recording=True, trading=True, show=False)

    engine = kis_engine.KiSEngine(price_codes=price_codes, orderbook_codes=orderbook_codes, simul_mode=False)
    # engine.add_strategy(test_st.KisOvernightMomentumStrategy)
    engine.run(recording=True, trading=False, show=True)