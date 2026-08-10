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
    price_codes = ["000660", "005930"]
    orderbook_codes = ["000660", "005930"]

    # simul_engine = kis_engine.KiSEngine(price_codes=price_codes, orderbook_codes=orderbook_codes, simul_mode=True)
    # simul_engine.add_strategy(test_st.KisOvernightMomentumStrategy)
    # simul_engine.run(recording=True, trading=True, show=False)

    engine = kis_engine.KiSEngine(price_codes=price_codes, orderbook_codes=orderbook_codes, simul_mode=False)
    # engine.add_strategy(test_st.KisOvernightMomentumStrategy)
    engine.run(recording=True, trading=False, show=True)