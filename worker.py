import os
import json
import time
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

import redis
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError

REDIS_HOST    = os.getenv("REDIS_HOST",    "localhost")
REDIS_PORT    = int(os.getenv("REDIS_PORT", "6379"))
STREAM_KEY    = os.getenv("STREAM_KEY",    "data_stream")
FAILED_KEY    = os.getenv("FAILED_KEY",    "data_stream_failed")
GROUP_NAME    = os.getenv("GROUP_NAME",    "workers")
CONSUMER_NAME = os.getenv("CONSUMER_NAME", "worker-1")
BATCH_SIZE    = int(os.getenv("BATCH_SIZE", "10"))
BLOCK_MS      = int(os.getenv("BLOCK_MS",  "1000"))

MONGO_USER    = os.getenv("MONGO_USER",    "admin")
MONGO_PASSWORD= os.getenv("MONGO_PASSWORD","changeme")
MONGO_HOST    = os.getenv("MONGO_HOST",    "localhost")
MONGO_PORT    = int(os.getenv("MONGO_PORT","27017"))
MONGO_DB      = os.getenv("MONGO_DB",     "grpcdata")

MAX_RETRIES   = 5

# Maps service name → MongoDB collection name
SERVICE_COLLECTIONS = {
    "alignment":    "alignment_reports",
    "bonding":      "bonding_reports",
    "creation":     "creation_reports",
    "pbi":          "pbi_reports",
    "pickup":       "pickup_reports",
    "transfer":     "transfer_reports",
    "preproduction":"preproduction_reports",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [worker] %(message)s")
log = logging.getLogger(__name__)


def get_mongo_db():
    uri = (f"mongodb://{MONGO_USER}:{MONGO_PASSWORD}"
           f"@{MONGO_HOST}:{MONGO_PORT}/?authSource=admin")
    client = MongoClient(uri)
    db = client[MONGO_DB]

    for coll_name in SERVICE_COLLECTIONS.values():
        coll = db[coll_name]
        coll.create_index([("received_at", ASCENDING)])
        coll.create_index([("production_id", ASCENDING)])
        coll.create_index([("component_id", ASCENDING)], sparse=True)

    log.info("MongoDB connected — db=%s, collections=%d",
             MONGO_DB, len(SERVICE_COLLECTIONS))
    return db


def write_packet(db, packet: dict):
    service     = packet[b"service"].decode()
    received_ms = int(packet[b"received_at"])
    data        = json.loads(packet[b"data"].decode())

    coll_name = SERVICE_COLLECTIONS.get(service)
    if not coll_name:
        log.warning("Unknown service '%s' — skipping", service)
        return

    doc = {
        "received_at":  datetime.fromtimestamp(received_ms / 1000, tz=timezone.utc),
        "service":      service,
        "production_id": None,
        "report":       data,
    }

    # preproduction: top-level id IS the productionId
    if service == "preproduction":
        doc["production_id"] = data.get("id", {}).get("id")
    else:
        doc["component_id"]  = data.get("id", {}).get("id")
        doc["production_id"] = data.get("productionId", {}).get("id")

    db[coll_name].insert_one(doc)


def process_with_retry(db, r, msg_id, packet):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            write_packet(db, packet)
            r.xack(STREAM_KEY, GROUP_NAME, msg_id)
            return True
        except PyMongoError as e:
            wait = 2 ** attempt
            log.warning("Attempt %d/%d failed: %s — retry in %ds",
                        attempt, MAX_RETRIES, e, wait)
            time.sleep(wait)

    log.error("Giving up on %s — moving to failed stream", msg_id)
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

    ensure_consumer_group(r)
    log.info("Worker '%s' ready on stream '%s'", CONSUMER_NAME, STREAM_KEY)

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
                process_with_retry(db, r, msg_id, packet)


if __name__ == "__main__":
    run()
