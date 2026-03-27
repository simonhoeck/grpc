# BESI gRPC → MongoDB Data Collection System

A data collection pipeline for BESI industrial machines. Machines send production and component reports over gRPC, a Redis Streams buffer absorbs bursts without loss, and a persistent worker writes each report into its own MongoDB collection. Grafana sits on top for dashboards.

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
- [Autostart Behavior](#autostart-behavior)
- [Backup & Recovery](#backup--recovery)
- [Scaling](#scaling)
- [Monitoring](#monitoring)
- [Publishing the Docker Images](#publishing-the-docker-images)

---

## Architecture

```
BESI Machine (gRPC client)
    │
    │  PushReport (unary)  ×7 services
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
    ├─── alignment   ──►  MongoDB  alignment_reports
    ├─── bonding     ──►  MongoDB  bonding_reports
    ├─── creation    ──►  MongoDB  creation_reports
    ├─── pbi         ──►  MongoDB  pbi_reports
    ├─── pickup      ──►  MongoDB  pickup_reports
    ├─── transfer    ──►  MongoDB  transfer_reports
    └─── preprod.    ──►  MongoDB  preproduction_reports
                                   │
                              Grafana dashboards
```

**Key design decisions:**

| Decision | Rationale |
|---|---|
| Redis Streams as buffer | Decouples gRPC ingestion rate from MongoDB write speed; survives spikes without packet loss |
| Consumer groups | Multiple worker instances can process the same stream in parallel for horizontal scaling |
| Failed-packet stream | Messages that cannot be written go to `data_stream_failed` instead of being discarded — nothing is silently lost |
| One collection per service | Each BESI service maps to its own collection, keeping report schemas separate and queries simple |
| Unary RPC | Each machine call is a single `PushReport` request/response — straightforward and stateless |

---

## Infrastructure / Docker

All six services — Redis, MongoDB, Grafana, Mongo Express, gRPC server, and worker — are managed by `docker-compose.yml` in the project root. Everything below assumes Docker Desktop is installed and running.

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

This starts all six containers in detached mode:

| Container | Image | Purpose |
|---|---|---|
| `grpc_redis` | `redis:7-alpine` | Stream buffer (AOF persistence enabled) |
| `grpc_mongodb` | `mongo:7` | Report collection storage |
| `grpc_mongo_express` | `mongo-express:latest` | Browser UI for MongoDB (port 8081) |
| `grpc_grafana` | `grafana/grafana:latest` | Dashboards (MongoDB datasource plugin pre-installed) |
| `grpc_server` | built from `Dockerfile` | gRPC server — receives all 7 BESI services |
| `grpc_worker` | built from `Dockerfile` | Redis consumer — writes reports to MongoDB |

Mongo Express and Grafana both wait for MongoDB's health check to pass before they start. The server and worker containers wait for Redis and MongoDB respectively.

### Verify all services are healthy

Start by checking the overall status of all containers:

```bash
docker compose ps
```

All containers should show `healthy` (Redis and MongoDB have health checks configured) or at minimum `Up`. Expected port bindings:

| Service | Container | Host port | Default URL |
|---|---|---|---|
| Redis | `grpc_redis` | 6379 | `redis://localhost:6379` |
| MongoDB | `grpc_mongodb` | 27017 | `mongodb://localhost:27017` |
| Mongo Express | `grpc_mongo_express` | 8081 | `http://localhost:8081` |
| Grafana | `grpc_grafana` | 3000 | `http://localhost:3000` |
| gRPC Server | `grpc_server` | 50051 | `grpc://localhost:50051` |

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
2. Click any of the 7 report collections to browse documents: `alignment_reports`, `bonding_reports`, `creation_reports`, `pbi_reports`, `pickup_reports`, `transfer_reports`, `preproduction_reports`.

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

> **Warning:** `docker compose down -v` permanently deletes the `redis_data`, `mongo_data`, and `grafana_data` volumes. All report data and Grafana dashboard configurations will be lost.

---

## Data Flow

1. A BESI machine calls one of the 7 gRPC services with a unary `PushReport(Report)` request.
2. The **gRPC server** serialises the report and pushes it onto the `data_stream` Redis Stream, then returns a `ReportReply`.
3. The **worker** reads from `data_stream` via a consumer group and routes each message to the correct MongoDB collection based on the service name (e.g. `alignment` → `alignment_reports`).
4. On write failure the message is moved to `data_stream_failed` and retried on the next cycle — nothing is silently lost.
5. Grafana queries MongoDB directly for dashboards and alerting.

---

## Proto Schema

The system implements 7 separate BESI gRPC services. Proto files live in two directories:

- `proto/component_data_collection/` — alignment, bonding, creation, pbi, pickup, transfer
- `proto/production_data_collection/` — preproduction

All services share the same unary RPC pattern:

```protobuf
service <Name>DataService {
  rpc PushReport(Report) returns (ReportReply);
}
```

| Service | Proto Package | MongoDB Collection |
|---|---|---|
| `AlignmentDataService` | `besi.component.services.alignment.v1_0_0` | `alignment_reports` |
| `BondingDataService` | `besi.component.services.bonding.v1_0_0` | `bonding_reports` |
| `CreationDataService` | `besi.component.services.creation.v1_0_0` | `creation_reports` |
| `PostBondInspectionDataService` | `besi.component.services.pbi.v1_0_0` | `pbi_reports` |
| `PickupDataService` | `besi.component.services.pickup.v1_0_0` | `pickup_reports` |
| `TransferDataService` | `besi.component.services.transfer.v1_0_0` | `transfer_reports` |
| `PreproductionDataService` | `besi.production.services.preproduction.v1_0_0` | `preproduction_reports` |

Generated Python stubs are stored in `generated/` and are produced by running `python generate_stubs.py`.

---

## MongoDB Collections

Each report lands in its own collection in the `grpcdata` database. All documents share the same structure:

```json
{
  "_id":           ObjectId("..."),
  "received_at":   ISODate("2025-03-23T10:15:32Z"),
  "service":       "alignment",
  "component_id":  "a1b2c3d4-...",        // present in all except preproduction
  "production_id": "prod-2025-001",
  "report":        { ... }               // full proto object as JSON
}
```

Indexes on: `received_at`, `production_id`, `component_id`

| Collection | Content |
|---|---|
| `alignment_reports` | Uplooking-camera adjustment, bonding-position adjustment, theta iterations |
| `bonding_reports` | Bond profile (stage positions) and optional axis traces |
| `creation_reports` | Component creation: tool name, lot info, source material |
| `pbi_reports` | Post-bond inspection: XY offset and theta offset |
| `pickup_reports` | Pickup process: flipper Z/theta, eject needle, vacuum traces |
| `transfer_reports` | Transfer from flipper to bonding head, component-center inspection |
| `preproduction_reports` | Machine name, serial number, PPID, component types used |

---

## Prerequisites

- Docker and Docker Compose
- Python 3.10+
- `grpcio`, `grpcio-tools`, `pymongo`, `redis`, `python-dotenv` Python packages

Install Python dependencies:

```bash
pip install -r requirements.txt
```

---

## Installation

1. Clone the repository:

```bash
git clone <repo-url>
cd gRPC
```

2. Start all services (Redis, MongoDB, Grafana, gRPC server, worker):

```bash
docker compose up -d
```

3. If proto files have changed, regenerate the Python stubs:

```bash
python generate_stubs.py
```

The generated stubs in `generated/` are already committed to the repository, so this step is only needed when proto files are modified.

---

## Configuration

All runtime settings are controlled via a `.env` file in the project root. Docker Compose and the server/worker load it automatically via `python-dotenv`.

**server.py**

| Variable | Default | Description |
|---|---|---|
| `GRPC_PORT` | `50051` | Port the gRPC server listens on |
| `MAX_WORKERS` | `50` | Thread pool size for the gRPC server |
| `REDIS_HOST` | `localhost` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `STREAM_KEY` | `data_stream` | Redis Stream key |

**worker.py**

| Variable | Default | Description |
|---|---|---|
| `REDIS_HOST` | `localhost` | Redis hostname |
| `MONGO_HOST` | `localhost` | MongoDB hostname |
| `MONGO_USER` | `admin` | MongoDB username |
| `MONGO_PASSWORD` | `changeme` | MongoDB password |
| `MONGO_DB` | `grpcdata` | Database name |
| `CONSUMER_NAME` | `worker-1` | Unique name per worker instance |
| `BATCH_SIZE` | `10` | Messages read per Redis cycle |

---

## Running the System

### Start everything

```bash
docker compose up -d
```

`docker compose up -d` starts all six containers — Redis, MongoDB, Grafana, Mongo Express, gRPC server, and worker — in detached mode. No separate `python server.py` or `python worker.py` commands are needed; both run inside Docker.

### Verify

```bash
docker compose ps
docker compose logs --tail=20
```

---

## Autostart Behavior

All six containers in `docker-compose.yml` are configured with `restart: unless-stopped`. This means:

| Event | Behavior |
|---|---|
| Container crash or OOM kill | Container restarts automatically |
| Docker daemon restart (e.g. after a PC reboot) | All containers start automatically |
| `docker compose down` | Containers stop and will **not** restart automatically |
| `docker compose stop <service>` | That container stops and will **not** restart automatically |

Once stopped with `docker compose down`, autostart is re-enabled by running `docker compose up -d` again.

```bash
# Stop all services (disables autostart until next `up -d`)
docker compose down

# Stop only the worker (disables autostart for worker only)
docker compose stop worker

# Re-enable autostart for all services
docker compose up -d
```

> **Note:** `restart: unless-stopped` means Docker itself manages restarts — no external service manager (systemd, Task Scheduler, etc.) is required on the host.

---

## Backup & Recovery

### Volumes with persistent data

| Volume | Contents | Backup priority |
|---|---|---|
| `mongo_data` | All 7 MongoDB collections (machine reports) | High — back up daily |
| `grafana_data` | Grafana dashboards and configuration | Medium — back up after changes |
| `redis_data` | Redis AOF/RDB persistence | Low — Redis is a buffer; data is already in MongoDB |

### MongoDB backup

```bash
# Dump the grpcdata database inside the container
docker exec grpc_mongodb mongodump \
  -u admin -p changeme \
  --authenticationDatabase admin \
  --db grpcdata \
  --out /tmp/backup

# Copy the dump to a local backup directory
docker cp grpc_mongodb:/tmp/backup ./backup/mongodb_$(date +%Y%m%d_%H%M%S)
```

> **Note:** Replace `admin` / `changeme` with your actual credentials from `.env`.

### Grafana backup

```bash
docker run --rm \
  -v grpc_grafana_data:/data \
  -v $(pwd)/backup:/backup \
  alpine tar czf /backup/grafana_$(date +%Y%m%d_%H%M%S).tar.gz -C /data .
```

### Redis backup (optional)

```bash
docker exec grpc_redis redis-cli BGSAVE
docker cp grpc_redis:/data/dump.rdb ./backup/redis_$(date +%Y%m%d_%H%M%S).rdb
```

### MongoDB restore

```bash
# Copy the backup into the container
docker cp ./backup/mongodb_DATUM grpc_mongodb:/tmp/restore

# Restore the grpcdata database
docker exec grpc_mongodb mongorestore \
  -u admin -p changeme \
  --authenticationDatabase admin \
  --db grpcdata \
  /tmp/restore/grpcdata
```

### Grafana restore

```bash
docker compose stop grafana

docker run --rm \
  -v grpc_grafana_data:/data \
  -v $(pwd)/backup:/backup \
  alpine tar xzf /backup/grafana_DATUM.tar.gz -C /data

docker compose up -d grafana
```

### Recommended backup strategy

- Run the MongoDB backup daily and store the output on a separate drive or network share.
- Run a Grafana backup after any dashboard changes.
- Store backups outside the project directory so that `docker compose down -v` cannot delete them.

---

## Scaling

The system is designed to scale horizontally at two independent layers.

### Scaling thresholds

| Connected clients | Recommended approach |
|---|---|
| 2 – 10 | Default configuration |
| 10 – 50 | Increase `MAX_WORKERS` (e.g. to 100) |
| 50 – 200 | Run multiple worker containers — they automatically share load via Redis consumer groups |
| 200+ | Run multiple gRPC server instances behind a load balancer (e.g. Nginx or Envoy), plus multiple workers |

### Running multiple workers

Each worker instance needs a unique consumer name within the group. Set `CONSUMER_NAME` in `docker-compose.yml` or via environment variable:

```bash
CONSUMER_NAME=worker-2 python worker.py
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
# Count documents in each collection
docker exec grpc_mongodb mongosh -u admin -p changeme \
  --authenticationDatabase admin grpcdata \
  --eval "['alignment','bonding','creation','pbi','pickup','transfer','preproduction'].forEach(s => print(s + ': ' + db[s + '_reports'].countDocuments({})))"
```

---

## Publishing the Docker Images

Once the images are built locally (`docker compose build`), you can push them to a registry so that any other machine can pull and run the stack without needing the source code.

### Option A — GitHub Container Registry (ghcr.io)

**1. Create a Personal Access Token**

Go to <https://github.com/settings/tokens> and create a classic token with the `write:packages` scope. Store it in an environment variable:

```bash
export CR_PAT=ghp_YourTokenHere
```

**2. Log in**

```bash
echo $CR_PAT | docker login ghcr.io -u simonhoeck --password-stdin
```

**3. Tag and push the server image**

```bash
docker tag grpc-server ghcr.io/simonhoeck/grpc-server:latest
docker push ghcr.io/simonhoeck/grpc-server:latest
```

**4. Tag and push the worker image**

```bash
docker tag grpc-worker ghcr.io/simonhoeck/grpc-worker:latest
docker push ghcr.io/simonhoeck/grpc-worker:latest
```

**5. Update `docker-compose.yml` on the target machine**

Replace the `build:` key with the registry image, for example:

```yaml
# Before (build from source)
grpc-server:
  build: .

# After (pull from registry)
grpc-server:
  image: ghcr.io/simonhoeck/grpc-server:latest
```

Apply the same change for the worker service.

---

### Option B — Docker Hub

**1. Log in**

```bash
docker login
```

You need a Docker Hub account at <https://hub.docker.com>.

**2. Tag and push the server image**

```bash
docker tag grpc-server simonhoeck/grpc-server:latest
docker push simonhoeck/grpc-server:latest
```

**3. Tag and push the worker image**

```bash
docker tag grpc-worker simonhoeck/grpc-worker:latest
docker push simonhoeck/grpc-worker:latest
```

**4. Update `docker-compose.yml` on the target machine**

```yaml
# Before (build from source)
grpc-server:
  build: .

# After (pull from Docker Hub)
grpc-server:
  image: simonhoeck/grpc-server:latest
```

Apply the same change for the worker service.

---

### Pulling and starting on another machine

Only `docker-compose.yml` and a `.env` file are needed on the target machine — no source code, no Python environment.

```bash
docker compose pull && docker compose up -d
```

`docker compose pull` fetches the latest image versions from the registry. `docker compose up -d` then starts all services in detached mode.

### Using published images on another machine

`docker-compose.yml` in this repository uses `build: .` for the server and worker — they are built locally from the `Dockerfile`. To deploy on a machine without the source code, push images to a registry first (Option A or B above), then update `docker-compose.yml` on the target machine to replace `build: .` with the registry image reference:

```yaml
# Before (build from source)
server:
  build: .

# After (pull from registry)
server:
  image: ghcr.io/simonhoeck/grpc-server:latest
```

Apply the same change for the worker service. Then copy `docker-compose.yml` and a `.env` file to the target machine and run:

```bash
docker compose pull
docker compose up -d
```

No source code or Python environment is needed on the target machine.


---

## License

See [LICENSE](LICENSE) for details.
