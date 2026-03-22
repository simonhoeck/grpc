# gRPC → MongoDB Data Acquisition System

A high-throughput data acquisition pipeline for IoT and industrial devices. Devices stream typed data packets over gRPC, a Redis Streams buffer absorbs bursts without loss, and a persistent worker writes everything into MongoDB — sensor and event data into a Time Series collection, binary payloads into GridFS. Grafana sits on top for dashboards.

---

## Table of Contents

- [Architecture](#architecture)
- [Infrastructure / Docker](#infrastructure--docker)
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
- [Connecting a Real Machine (Anlage)](#connecting-a-real-machine-anlage)

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

## Infrastructure / Docker

The three backing services — Redis, MongoDB, and Grafana — are managed by `docker-compose.yml` in the project root. Everything below assumes Docker Desktop is installed and running.

### Prerequisites

- **Docker Desktop** (Windows / macOS) or Docker Engine + Compose plugin (Linux), running before any `docker compose` command.

### Configure credentials

Create a `.env` file in the project root. Docker Compose reads it automatically and substitutes the values into the service definitions.

```dotenv
# .env  — do not commit this file
MONGO_USER=admin
MONGO_PASSWORD=changeme
GRAFANA_USER=admin
GRAFANA_PASSWORD=admin
```

> **Note:** The values above are the built-in defaults from `docker-compose.yml`. Change them before running in any environment that is reachable from a network.

The variables map to the following container settings:

| Variable | Container env var | Service |
|---|---|---|
| `MONGO_USER` | `MONGO_INITDB_ROOT_USERNAME` | MongoDB |
| `MONGO_PASSWORD` | `MONGO_INITDB_ROOT_PASSWORD` | MongoDB |
| `GRAFANA_USER` | `GF_SECURITY_ADMIN_USER` | Grafana |
| `GRAFANA_PASSWORD` | `GF_SECURITY_ADMIN_PASSWORD` | Grafana |

### Start all services

```bash
docker compose up -d
```

This starts four containers in detached mode:

| Container | Image | Purpose |
|---|---|---|
| `grpc_redis` | `redis:7-alpine` | Stream buffer (AOF persistence enabled) |
| `grpc_mongodb` | `mongo:7` | Time Series + GridFS storage |
| `grpc_mongo_express` | `mongo-express:latest` | Browser UI for MongoDB (port 8081) |
| `grpc_grafana` | `grafana/grafana:latest` | Dashboards (MongoDB datasource plugin pre-installed) |

Mongo Express and Grafana both wait for MongoDB's health check to pass before they start.

### Verify all services are healthy

Start by checking the overall status of all containers:

```bash
docker compose ps
```

All three containers should show `healthy` (Redis and MongoDB have health checks configured) or at minimum `Up`. Expected port bindings:

| Service | Container | Host port | Default URL |
|---|---|---|---|
| Redis | `grpc_redis` | 6379 | `redis://localhost:6379` |
| MongoDB | `grpc_mongodb` | 27017 | `mongodb://localhost:27017` |
| Mongo Express | `grpc_mongo_express` | 8081 | `http://localhost:8081` |
| Grafana | `grpc_grafana` | 3000 | `http://localhost:3000` |

If any container looks wrong, check its recent output:

```bash
docker compose logs --tail=20
```

To verify each service individually, use the commands below.

#### Redis

```bash
# Should return: PONG
docker exec grpc_redis redis-cli ping
```

```bash
# Should print the Redis server version
docker exec grpc_redis redis-cli info server | grep redis_version
```

#### MongoDB

```bash
# Should return: { ok: 1 }
docker exec grpc_mongodb mongosh \
  -u admin -p changeme \
  --authenticationDatabase admin \
  --eval "db.adminCommand('ping')"
```

```bash
# Lists all databases — confirms authentication works
docker exec grpc_mongodb mongosh \
  -u admin -p changeme \
  --authenticationDatabase admin \
  --eval "show dbs"
```

> **Note:** Replace `admin` / `changeme` with the values from your `.env` file if you changed the defaults.

#### Grafana

Open `http://localhost:3000` in your browser and log in with `admin` / `admin` (or the credentials from your `.env`). On first login Grafana may prompt you to change the password.

Alternatively, check the health endpoint from the command line — no login required:

```bash
# Should return: {"database":"ok", ...}
curl -s http://localhost:3000/api/health
```

### Open Grafana

Navigate to `http://localhost:3000` in your browser. Log in with the credentials you set in `.env` (default: `admin` / `admin`). On first login Grafana may prompt you to change the password.

The MongoDB datasource plugin (`grafana-mongodb-datasource`) is installed automatically at container startup. Add it under **Connections > Data sources** and point it at `mongodb://grpc_mongodb:27017` using the same `MONGO_USER` / `MONGO_PASSWORD` values.

### Viewing data in the browser (Mongo Express)

Mongo Express is included in `docker-compose.yml` and starts automatically alongside the other services:

```bash
docker compose up -d
```

Open `http://localhost:8081` in your browser — no login is required (basic auth is disabled in the compose configuration).

**Navigating to your data:**

1. In the left-hand database list, click **grpcdata**.
2. Click **measurements** to browse sensor and event documents stored in the Time Series collection.
3. The **fs.files** and **fs.chunks** collections contain the GridFS binary payloads. Use `fs.files` to find a file by metadata (device ID, timestamp) and `fs.chunks` for the raw binary chunks.

No installation is needed beyond Docker — Mongo Express runs entirely as a container and connects to `grpc_mongodb` over the internal Docker network.

### Stop services

```bash
# Stop and remove containers, keep all volume data intact
docker compose down
```

```bash
# Stop and remove containers AND delete all stored data (Redis, MongoDB, Grafana)
docker compose down -v
```

> **Warning:** `docker compose down -v` permanently deletes the `redis_data`, `mongo_data`, and `grafana_data` volumes. All measurements, binary files, and Grafana dashboard configurations will be lost.

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

| Script | What it sends | Passing result |
|---|---|---|
| `tests/test_single.py` | 100 sensor packets from one client | `[test_single] success=True  (100 packets sent)` |
| `tests/test_multi.py` | 100 sensor packets each from 20 concurrent clients (2 000 total) | `[test_multi] 20/20 clients OK — 2000 packets sent, 0 failed` |
| `tests/test_mixed.py` | 10 sensor + 5 event + 3 binary packets in a single stream | `[test_mixed] success=True  (10 sensor + 5 event + 3 binary)` |
| `tests/test_load.py` | 1 000 sensor packets each from 50 concurrent clients (50 000 total) | `[test_load] 50/50 clients OK` plus throughput summary |

### Prerequisites

Both `server.py` and `worker.py` must be running before you execute any test. Start them in separate terminals:

```bash
# Terminal 1
python server.py

# Terminal 2
python worker.py
```

Docker infrastructure (Redis + MongoDB) must also be up:

```bash
docker compose up -d
```

### Run the tests

```bash
python tests/test_single.py
python tests/test_multi.py
python tests/test_mixed.py
python tests/test_load.py
```

You can override the server address with the `GRPC_SERVER` environment variable (default: `localhost:50051`):

```bash
GRPC_SERVER=192.168.1.10:50051 python tests/test_single.py
```

### Expected output

**test_single.py**

```
[test_single] success=True  (100 packets sent)
```

**test_multi.py**

```
[test_multi] 20/20 clients OK — 2000 packets sent, 0 failed
```

If any individual client call returns `success=False`, that client's result is printed before the summary:

```
[test_multi] client 7 FAILED
[test_multi] 19/20 clients OK — 2000 packets sent, 1 failed
```

**test_mixed.py**

```
[test_mixed] success=True  (10 sensor + 5 event + 3 binary)
```

**test_load.py**

`test_load.py` prints a progress line at the start and a throughput summary at the end:

```
[test_load] Starting 50 clients × 1000 packets = 50000 total ...
[test_load] 50/50 clients OK
[test_load] Total packets : 50000
[test_load] Total time    : 8.3s
[test_load] Throughput    : 6024 packets/s
[test_load] Avg per client: 7.41s
```

Exact timing figures vary by hardware; the important indicators are `50/50 clients OK` and `0 failed`.

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

## Connecting a Real Machine (Anlage)

This section describes how to run `producer.py` directly on an industrial machine or embedded PC so it streams live measurements to the server.

### Files needed on the machine

Copy only these three files to the machine — nothing else is required:

- `data_pb2.py`
- `data_pb2_grpc.py`
- `producer.py`

The generated stub files (`data_pb2.py`, `data_pb2_grpc.py`) are already committed to the repository. There is no need to install `grpcio-tools` or run `protoc` on the machine.

### Install on the machine

```bash
pip install grpcio
```

Only `grpcio` (the runtime) is needed. `grpcio-tools` is a development-only dependency for regenerating stubs and is not required on the machine.

### Configuration via environment variables

| Variable | Default | Description |
|---|---|---|
| `GRPC_SERVER` | `localhost:50051` | IP address and port of the server PC (e.g. `192.168.1.10:50051`) |
| `DEVICE_ID` | `anlage-001` | Unique name that identifies this machine in the database |
| `INTERVAL` | `1.0` | Measurement interval in seconds |

Set variables before starting the producer, for example:

```bash
GRPC_SERVER=192.168.1.10:50051 DEVICE_ID=presse-01 INTERVAL=0.5 python producer.py
```

On Windows, set them as system environment variables or use a `.env` loader.

### What to customize

Open `producer.py` and fill in the two marked functions:

**`read_sensors()`** — replace the placeholder values with real hardware reads. Supported protocols include Modbus, OPC-UA, Serial/RS-232, GPIO, and any Python-accessible interface. The function must return a `dict` of measurement values; the keys become the field names stored in MongoDB.

**`read_events()`** — optional. Return a list of event dicts (alarms, status changes, error codes) whenever something noteworthy occurs, or `None` if there is nothing to report. Each dict is stored as a separate `event` packet in the `measurements` collection.

### How to run

```bash
python producer.py
```

The producer connects to the gRPC server and streams packets continuously. Log output is written to stdout.

### Windows Firewall

The server PC needs port 50051 open for inbound TCP connections. Run the following command once on the server PC with administrator privileges:

```
netsh advfirewall firewall add rule name="gRPC" dir=in action=allow protocol=TCP localport=50051
```

### Auto-reconnect

If the connection to the server is lost (network interruption, server restart), `producer.py` automatically waits 5 seconds and then reconnects. No manual intervention is required.

---

## License

See [LICENSE](LICENSE) for details.
