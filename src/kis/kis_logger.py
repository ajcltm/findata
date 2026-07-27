import logging
import os

def setup_logger(dir):
    logger = logging.getLogger("kis")    # 앱 전체 공통 이름

    if logger.handlers:                    # 이미 설정됐으면 중복 방지
        return logger

    logger.setLevel(logging.DEBUG)

    os.makedirs("logs", exist_ok=True)
    handler = logging.FileHandler(dir / "kis.log", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(filename)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger