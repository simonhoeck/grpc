"""Mixed data types — sensor, event, and binary packets in one stream."""
import json
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import grpc
import data_pb2
import data_pb2_grpc

SERVER = os.getenv("GRPC_SERVER", "localhost:50051")


def generate(device_id="device-mixed"):
    ts = int(time.time() * 1000)

    # 10 sensor packets
    for i in range(10):
        yield data_pb2.DataPacket(
            type="sensor",
            timestamp=ts + i,
            device_id=device_id,
            payload=json.dumps({"temp": 20.0 + i, "humidity": 50 + i}).encode(),
        )

    # 5 event packets
    for i in range(5):
        yield data_pb2.DataPacket(
            type="event",
            timestamp=ts + 100 + i,
            device_id=device_id,
            payload=json.dumps({"event": "alarm", "code": i}).encode(),
        )

    # 3 binary packets (simulated image bytes)
    for i in range(3):
        yield data_pb2.DataPacket(
            type="binary",
            timestamp=ts + 200 + i,
            device_id=device_id,
            payload=os.urandom(256),
        )


def run():
    with grpc.insecure_channel(SERVER) as channel:
        stub = data_pb2_grpc.DataServiceStub(channel)
        ack = stub.SendData(generate())
        print(f"[test_mixed] success={ack.success}  (10 sensor + 5 event + 3 binary)")
        assert ack.success, "Server returned success=False"


if __name__ == "__main__":
    run()
