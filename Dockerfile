FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto/         ./proto/
COPY data_pb2.py    .
COPY data_pb2_grpc.py .
COPY server.py      .
COPY worker.py      .
