FROM python:3.11-slim

WORKDIR /usr/src/app

COPY requirements.txt ./

RUN apt-get update && apt-get install -y \
    build-essential gfortran \
    && pip install --no-cache-dir -r requirements.txt


COPY . .


CMD [ "uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", "8080" ]
