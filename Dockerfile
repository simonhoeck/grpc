FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto/      ./proto/
COPY generated/  ./generated/
COPY server.py   .
COPY worker.py   .
