"""Single client — 100 sensor packets."""
import json
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import grpc
import data_pb2
import data_pb2_grpc

SERVER = os.getenv("GRPC_SERVER", "localhost:50051")
PACKETS = 100


def generate(device_id="device-001"):
    for i in range(PACKETS):
        yield data_pb2.DataPacket(
            type="sensor",
            timestamp=int(time.time() * 1000),
            device_id=device_id,
            payload=json.dumps({"index": i, "value": i * 0.5}).encode(),
        )


def run():
    with grpc.insecure_channel(SERVER) as channel:
        stub = data_pb2_grpc.DataServiceStub(channel)
        ack = stub.SendData(generate())
        print(f"[test_single] success={ack.success}  ({PACKETS} packets sent)")
        assert ack.success, "Server returned success=False"


if __name__ == "__main__":
    run()
