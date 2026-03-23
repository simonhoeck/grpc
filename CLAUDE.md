# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **gRPC → MongoDB Data Acquisition System** for collecting sensor, event, and binary data from IoT/industrial devices. The full architecture and setup is documented in `grpc-mongodb-setup.pdf`.

## Architecture

```
Producer (gRPC client) → gRPC Server (port 50051, 50 workers)
  → Redis Streams (data_stream key, consumer groups)
  → Worker/Consumer → MongoDB (Time Series + GridFS)
  → Grafana (dashboards)
```

**Key design choices:**
- Redis Streams acts as a no-loss buffer between gRPC server and MongoDB writer
- Failed packets go to `data_stream_failed` stream (not discarded)
- MongoDB uses Time Series collections (granularity=seconds) for sensor/event data
- Binary data (type="binary") goes to GridFS, not the measurements collection
- Worker uses consumer groups for horizontal scaling

## Core Files

| File | Purpose |
|------|---------|
| `proto/data.proto` | Proto3 definition: `DataPacket` (type, timestamp, device_id, payload) + `DataService.SendData` |
| `server.py` | gRPC server — receives streaming DataPackets, pushes to Redis |
| `worker.py` | Redis consumer — reads stream, writes to MongoDB/GridFS with retry |
| `docker-compose.yml` | Redis, MongoDB (with auth), Grafana |
| `tests/test_single.py` | 100 packets, single client |
| `tests/test_multi.py` | 20 concurrent clients |
| `tests/test_mixed.py` | Mixed types: sensor/event/binary |
| `tests/test_load.py` | 50 clients × 1000 packets |

## Infrastructure

Start all services:
```bash
docker compose up -d
```

## Development Setup

Generate Python gRPC stubs from proto:
```bash
python -m grpc_tools.protoc -I./proto --python_out=. --grpc_python_out=. proto/data.proto
```

Run server:
```bash
python server.py
```

Run worker:
```bash
python worker.py
```

## Running Tests

```bash
python tests/test_single.py    # baseline single client
python tests/test_multi.py     # concurrency test
python tests/test_mixed.py     # data type coverage
python tests/test_load.py      # stress test (50 clients × 1000 packets)
```

## Proto Schema

```protobuf
message DataPacket {
  string type = 1;       // "sensor", "event", "binary"
  int64 timestamp = 2;   // Unix epoch ms
  string device_id = 3;
  bytes payload = 4;     // JSON-encoded for sensor/event; raw bytes for binary
}
message Ack { bool success = 1; }
service DataService { rpc SendData(stream DataPacket) returns (Ack); }
```

## Proto Best Practices

- **Never re-use a tag number** (`= 1`, `= 2`, etc.) — it breaks deserialization of any serialized data in logs or old clients
- **Reserve deleted fields**: when removing a field, add `reserved <number>;` (e.g. `reserved 3;`) so the number cannot be accidentally reused
- **Reserve deleted names**: also add `reserved "fieldname";` to prevent name recycling
- **Enums need a zero default**: always include `UNDEFINED = 0` as the first enum value to handle the unset case
- **`repeated` fields**: use for zero-or-more list fields; maps to Python lists in generated stubs
- **Nested messages**: messages can embed other message types for hierarchical data

## Scaling Thresholds

| Client count | Approach |
|---|---|
| 2–10 | Default config |
| 10–50 | Tune `max_workers` |
| 50–200 | Multiple worker consumers (consumer groups) |
| 200+ | Multiple gRPC server instances + load balancer |

## MongoDB Collections

- `measurements` — Time Series collection, indexed on `(timestamp, device_id, type)`, stores sensor and event data
- GridFS — binary payloads

## Key Verify Commands

```bash
# Check Redis stream length
redis-cli xlen data_stream

# Check MongoDB measurements count
mongosh --eval 'db.measurements.countDocuments({})'

# Check failed stream
redis-cli xlen data_stream_failed
```
