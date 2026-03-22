# gRPC → MongoDB Data Acquisition System

A high-throughput data acquisition pipeline for IoT and industrial devices. Devices stream typed data packets over gRPC, a Redis Streams buffer absorbs bursts without loss, and a persistent worker writes everything into MongoDB — sensor and event data into a Time Series collection, binary payloads into GridFS. Grafana sits on top for dashboards.

---

## Table of Contents

- [Architecture](#architecture)
- [Data Flow](#data-flow)
- [Proto Schema](#proto-schema)
- [MongoDB Collections](#mongodb-collections)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the System](#running-the-system)
- [Usage Examples](#usage-examples)
- [Testing](#testing)
- [Scaling](#scaling)
- [Monitoring](#monitoring)

---

## Architecture

```
Producer (gRPC client)
    │
    │  stream DataPacket
    ▼
gRPC Server  (port 50051, 50 workers)
    │
    │  XADD  data_stream
    ▼
Redis Streams  (consumer groups, no-loss buffer)
    │
    │  XREADGROUP
    ▼
Worker / Consumer
    ├─── sensor / event  ──►  MongoDB  measurements  (Time Series)
    └─── binary          ──►  MongoDB  GridFS
                                   │
                              Grafana dashboards
```

**Key design decisions:**

| Decision | Rationale |
|---|---|
| Redis Streams as buffer | Decouples gRPC ingestion rate from MongoDB write speed; survives spikes without packet loss |
| Consumer groups | Multiple worker instances can process the same stream in parallel for horizontal scaling |
| Failed-packet stream | Messages that cannot be written go to `data_stream_failed` instead of being discarded — nothing is silently lost |
| Time Series collection | MongoDB native time series gives automatic bucketing and efficient range queries on `(timestamp, device_id, type)` |
| GridFS for binary | Binary payloads (firmware images, waveforms, etc.) are too large for a document field; GridFS handles chunking transparently |

---

## Data Flow

1. A device (the **producer**) opens a client-streaming gRPC call to `DataService.SendData`.
2. It sends one or more `DataPacket` messages — each with a type, timestamp, device ID, and payload.
3. The **gRPC server** pushes each packet onto the `data_stream` Redis Stream and returns an `Ack`.
4. The **worker** reads from `data_stream` via a consumer group:
   - `type = "sensor"` or `type = "event"` → inserted into the `measurements` Time Series collection.
   - `type = "binary"` → stored in GridFS; a reference document is added to `measurements`.
   - On write failure the message is moved to `data_stream_failed` and retried on the next cycle.
5. Grafana queries MongoDB directly for dashboards and alerting.

---

## Proto Schema

File: `proto/data.proto`

```protobuf
syntax = "proto3";

message DataPacket {
  string type      = 1;  // "sensor" | "event" | "binary"
  int64  timestamp = 2;  // Unix epoch, milliseconds
  string device_id = 3;
  bytes  payload   = 4;  // JSON-encoded for sensor/event; raw bytes for binary
}

message Ack {
  bool success = 1;
}

service DataService {
  rpc SendData(stream DataPacket) returns (Ack);
}
```

**Payload conventions:**

| Type | Payload format | Example |
|---|---|---|
| `sensor` | JSON object | `{"temperature": 23.4, "humidity": 61}` |
| `event` | JSON object | `{"code": "E_OVERHEAT", "severity": "warn"}` |
| `binary` | Raw bytes | firmware image, ADC waveform, camera frame |

---

## MongoDB Collections

### `measurements` (Time Series)

- Granularity: `seconds`
- Time field: `timestamp`
- Meta fields: `device_id`, `type`
- Compound index on `(timestamp, device_id, type)`
- Stores all `sensor` and `event` packets, plus reference documents for binary uploads

### GridFS (`fs.files` / `fs.chunks`)

- Stores raw binary payloads
- Each file's metadata includes `device_id`, `timestamp`, and `type = "binary"`
- The `measurements` document for a binary packet contains the GridFS `file_id` for cross-referencing

---

## Prerequisites

- Docker and Docker Compose
- Python 3.10+
- `grpcio`, `grpcio-tools`, `pymongo`, `redis` Python packages

Install Python dependencies:

```bash
pip install grpcio grpcio-tools pymongo redis
```

---

## Installation

1. Clone the repository:

```bash
git clone <repo-url>
cd gRPC
```

2. Start infrastructure (Redis, MongoDB, Grafana):

```bash
docker compose up -d
```

3. Generate Python gRPC stubs from the proto definition:

```bash
python -m grpc_tools.protoc \
  -I./proto \
  --python_out=. \
  --grpc_python_out=. \
  proto/data.proto
```

This produces `data_pb2.py` and `data_pb2_grpc.py` in the project root.

---

## Configuration

All runtime settings are controlled via environment variables or a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `GRPC_PORT` | `50051` | Port the gRPC server listens on |
| `GRPC_MAX_WORKERS` | `50` | Thread pool size for the gRPC server |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `REDIS_STREAM_KEY` | `data_stream` | Primary Redis Stream key |
| `REDIS_FAILED_KEY` | `data_stream_failed` | Stream for undeliverable messages |
| `REDIS_CONSUMER_GROUP` | `workers` | Consumer group name |
| `MONGO_URL` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB` | `daq` | Database name |

---

## Running the System

### 1. Start infrastructure

```bash
docker compose up -d
```

### 2. Start the gRPC server

```bash
python server.py
```

The server listens on `0.0.0.0:50051` and pushes incoming packets to Redis.

### 3. Start the worker

```bash
python worker.py
```

The worker joins the `workers` consumer group on `data_stream` and writes to MongoDB. Run multiple instances for parallel processing.

---

## Usage Examples

### Sending sensor data from a Python client

```python
import grpc
import data_pb2
import data_pb2_grpc
import time
import json

channel = grpc.insecure_channel("localhost:50051")
stub = data_pb2_grpc.DataServiceStub(channel)

def generate_packets():
    for i in range(10):
        payload = json.dumps({"temperature": 20.0 + i, "humidity": 50 + i}).encode()
        yield data_pb2.DataPacket(
            type="sensor",
            timestamp=int(time.time() * 1000),
            device_id="device-001",
            payload=payload,
        )

ack = stub.SendData(generate_packets())
print("Success:", ack.success)
```

### Sending an event

```python
payload = json.dumps({"code": "E_STARTUP", "severity": "info"}).encode()

ack = stub.SendData(iter([
    data_pb2.DataPacket(
        type="event",
        timestamp=int(time.time() * 1000),
        device_id="device-001",
        payload=payload,
    )
]))
```

### Sending binary data

```python
with open("firmware_v2.bin", "rb") as f:
    raw = f.read()

ack = stub.SendData(iter([
    data_pb2.DataPacket(
        type="binary",
        timestamp=int(time.time() * 1000),
        device_id="device-001",
        payload=raw,
    )
]))
```

---

## Testing

The `tests/` directory contains four test scripts covering different scenarios:

| Script | Scenario |
|---|---|
| `tests/test_single.py` | 100 packets, single client — baseline correctness check |
| `tests/test_multi.py` | 20 concurrent clients — concurrency and ordering |
| `tests/test_mixed.py` | Mixed types: sensor, event, binary — data routing coverage |
| `tests/test_load.py` | 50 clients × 1000 packets — stress / throughput test |

Run any test directly:

```bash
python tests/test_single.py
python tests/test_multi.py
python tests/test_mixed.py
python tests/test_load.py
```

---

## Scaling

The system is designed to scale horizontally at two independent layers.

### Scaling thresholds

| Connected clients | Recommended approach |
|---|---|
| 2 – 10 | Default configuration |
| 10 – 50 | Increase `GRPC_MAX_WORKERS` (e.g. to 100) |
| 50 – 200 | Run multiple `worker.py` instances — they automatically share load via Redis consumer groups |
| 200+ | Run multiple gRPC server instances behind a load balancer (e.g. Nginx or Envoy), plus multiple workers |

### Running multiple workers

Each worker instance needs a unique consumer name within the group:

```bash
WORKER_NAME=worker-1 python worker.py
WORKER_NAME=worker-2 python worker.py
```

Redis consumer groups guarantee that each message is delivered to exactly one worker, preventing duplicate writes.

---

## Monitoring

### Grafana

Grafana is available at `http://localhost:3000` after `docker compose up -d`.

### Redis stream health

```bash
# Number of messages waiting in the main stream
redis-cli xlen data_stream

# Number of messages in the failed stream (should stay at 0)
redis-cli xlen data_stream_failed

# Consumer group lag (pending messages per consumer)
redis-cli xpending data_stream workers - + 10
```

### MongoDB document count

```bash
# Total measurements stored
mongosh --eval 'db.measurements.countDocuments({})'

# Sensor data only
mongosh --eval 'db.measurements.countDocuments({type: "sensor"})'

# Binary file count (GridFS)
mongosh --eval 'db.fs.files.countDocuments({})'
```

---

## License

See [LICENSE](LICENSE) for details.
