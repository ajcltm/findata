FROM python:3.12-slim

ENV TZ=Asia/Seoul

# 에러를 방지하기 위해 기존 localtime을 지우고 새로 연결합니다.
RUN apt-get update && apt-get install -y tzdata && rm -f /etc/localtime && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone && apt-get clean

WORKDIR /app

# 설정 파일 및 라이브러리 먼저 설치 (빌드 최적화)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 전체 복사
COPY . .

# 패키지 설치
RUN pip install -e .

RUN python -m ipykernel install --user --name findata-venv --display-name "Python (findata-venv)"

# 컨테이너 실행 시 쉘로 진입
CMD ["/bin/bash"]