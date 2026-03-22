import os
import json
import time
import logging
from datetime import datetime, timezone

import redis
from pymongo import MongoClient
from pymongo.errors import PyMongoError
import gridfs

REDIS_HOST     = os.getenv("REDIS_HOST",     "localhost")
REDIS_PORT     = int(os.getenv("REDIS_PORT", "6379"))
STREAM_KEY     = os.getenv("STREAM_KEY",     "data_stream")
FAILED_KEY     = os.getenv("FAILED_KEY",     "data_stream_failed")
GROUP_NAME     = os.getenv("GROUP_NAME",     "workers")
CONSUMER_NAME  = os.getenv("CONSUMER_NAME",  "worker-1")
BATCH_SIZE     = int(os.getenv("BATCH_SIZE", "10"))
BLOCK_MS       = int(os.getenv("BLOCK_MS",   "1000"))

MONGO_USER     = os.getenv("MONGO_USER",     "admin")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD", "changeme")
MONGO_HOST     = os.getenv("MONGO_HOST",     "localhost")
MONGO_PORT     = int(os.getenv("MONGO_PORT", "27017"))
MONGO_DB       = os.getenv("MONGO_DB",       "grpcdata")

MAX_RETRIES    = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(message)s")
log = logging.getLogger(__name__)


def get_mongo_db():
    uri = f"mongodb://{MONGO_USER}:{MONGO_PASSWORD}@{MONGO_HOST}:{MONGO_PORT}/?authSource=admin"
    client = MongoClient(uri)
    db = client[MONGO_DB]
    # Ensure Time Series collection exists
    if "measurements" not in db.list_collection_names():
        db.create_collection(
            "measurements",
            timeseries={
                "timeField":   "timestamp",
                "metaField":   "device_id",
                "granularity": "seconds",
            }
        )
        log.info("Created time series collection 'measurements'")
    return db


def write_packet(db, fs, packet: dict):
    ptype     = packet[b"type"].decode()
    device_id = packet[b"device_id"].decode()
    timestamp = int(packet[b"timestamp"])
    payload   = packet[b"payload"]

    dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)

    if ptype == "binary":
        fs.put(payload, device_id=device_id, timestamp=dt, type=ptype)
    else:
        data = json.loads(payload.decode())
        db.measurements.insert_one({
            "timestamp": dt,
            "device_id": device_id,
            "type":      ptype,
            "data":      data,
        })


def process_with_retry(db, fs, r, msg_id, packet):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            write_packet(db, fs, packet)
            r.xack(STREAM_KEY, GROUP_NAME, msg_id)
            return True
        except PyMongoError as e:
            wait = 2 ** attempt
            log.warning("Attempt %d/%d failed: %s — retrying in %ds", attempt, MAX_RETRIES, e, wait)
            time.sleep(wait)

    log.error("Giving up on message %s — moving to failed stream", msg_id)
    r.xadd(FAILED_KEY, packet)
    r.xack(STREAM_KEY, GROUP_NAME, msg_id)
    return False


def ensure_consumer_group(r):
    try:
        r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        log.info("Consumer group '%s' created", GROUP_NAME)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def run():
    r  = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
    db = get_mongo_db()
    fs = gridfs.GridFS(db)

    ensure_consumer_group(r)
    log.info("Worker '%s' started, listening on stream '%s'", CONSUMER_NAME, STREAM_KEY)

    while True:
        messages = r.xreadgroup(
            GROUP_NAME, CONSUMER_NAME,
            {STREAM_KEY: ">"},
            count=BATCH_SIZE,
            block=BLOCK_MS,
        )
        if not messages:
            continue

        for _, entries in messages:
            for msg_id, packet in entries:
                process_with_retry(db, fs, r, msg_id, packet)


if __name__ == "__main__":
    run()
