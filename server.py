import os
import json
import logging
from concurrent import futures

import grpc
import redis

import data_pb2
import data_pb2_grpc

GRPC_PORT   = os.getenv("GRPC_PORT",   "50051")
REDIS_HOST  = os.getenv("REDIS_HOST",  "localhost")
REDIS_PORT  = int(os.getenv("REDIS_PORT", "6379"))
STREAM_KEY  = os.getenv("STREAM_KEY",  "data_stream")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "50"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [server] %(message)s")
log = logging.getLogger(__name__)


class DataServicer(data_pb2_grpc.DataServiceServicer):

    def __init__(self):
        self.redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)

    def SendData(self, request_iterator, context):
        count = 0
        try:
            for packet in request_iterator:
                self.redis.xadd(STREAM_KEY, {
                    "type":      packet.type,
                    "timestamp": packet.timestamp,
                    "device_id": packet.device_id,
                    "payload":   packet.payload,
                })
                count += 1
        except Exception as e:
            log.error("Error processing stream: %s", e)
            return data_pb2.Ack(success=False)

        log.info("Received %d packets from %s", count, context.peer())
        return data_pb2.Ack(success=True)


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))
    data_pb2_grpc.add_DataServiceServicer_to_server(DataServicer(), server)
    server.add_insecure_port(f"0.0.0.0:{GRPC_PORT}")
    server.start()
    log.info("gRPC server listening on port %s (%d workers)", GRPC_PORT, MAX_WORKERS)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
