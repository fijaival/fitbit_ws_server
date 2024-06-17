FROM python:3.8-alpine

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN apk add --no-cache build-base \
    && pip install --no-cache-dir --trusted-host pypi.python.org -r requirements.txt \
    && apk del build-base

COPY . .


CMD [ "uvicorn", "main:app", "--reload", "--host", "0.0.0.0", "--port", "8080" ]
