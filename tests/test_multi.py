"""20 concurrent clients — each sends 100 sensor packets."""
import json
import time
import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import grpc
import data_pb2
import data_pb2_grpc

SERVER  = os.getenv("GRPC_SERVER", "localhost:50051")
CLIENTS = 20
PACKETS = 100


def generate(device_id):
    for i in range(PACKETS):
        yield data_pb2.DataPacket(
            type="sensor",
            timestamp=int(time.time() * 1000),
            device_id=device_id,
            payload=json.dumps({"index": i, "value": i * 1.1}).encode(),
        )


def client_task(client_id):
    device_id = f"device-{client_id:03d}"
    with grpc.insecure_channel(SERVER) as channel:
        stub = data_pb2_grpc.DataServiceStub(channel)
        ack = stub.SendData(generate(device_id))
        return client_id, ack.success


def run():
    ok = failed = 0
    with ThreadPoolExecutor(max_workers=CLIENTS) as pool:
        futures = [pool.submit(client_task, i) for i in range(CLIENTS)]
        for f in as_completed(futures):
            cid, success = f.result()
            if success:
                ok += 1
            else:
                failed += 1
                print(f"[test_multi] client {cid} FAILED")

    total = CLIENTS * PACKETS
    print(f"[test_multi] {ok}/{CLIENTS} clients OK — {total} packets sent, {failed} failed")
    assert failed == 0, f"{failed} client(s) failed"


if __name__ == "__main__":
    run()
