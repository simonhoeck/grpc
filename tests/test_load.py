"""Load test — 50 concurrent clients × 1000 packets each."""
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
CLIENTS = 50
PACKETS = 1000


def generate(device_id):
    for i in range(PACKETS):
        yield data_pb2.DataPacket(
            type="sensor",
            timestamp=int(time.time() * 1000),
            device_id=device_id,
            payload=json.dumps({"index": i, "value": i * 0.1}).encode(),
        )


def client_task(client_id):
    device_id = f"load-device-{client_id:03d}"
    with grpc.insecure_channel(SERVER) as channel:
        stub = data_pb2_grpc.DataServiceStub(channel)
        t0  = time.time()
        ack = stub.SendData(generate(device_id))
        elapsed = time.time() - t0
        return client_id, ack.success, elapsed


def run():
    ok = failed = 0
    times = []

    print(f"[test_load] Starting {CLIENTS} clients × {PACKETS} packets = {CLIENTS * PACKETS} total ...")
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=CLIENTS) as pool:
        futures = [pool.submit(client_task, i) for i in range(CLIENTS)]
        for f in as_completed(futures):
            cid, success, elapsed = f.result()
            times.append(elapsed)
            if success:
                ok += 1
            else:
                failed += 1
                print(f"[test_load] client {cid} FAILED")

    total_time  = time.time() - t_start
    total_pkts  = CLIENTS * PACKETS
    throughput  = total_pkts / total_time

    print(f"[test_load] {ok}/{CLIENTS} clients OK")
    print(f"[test_load] Total packets : {total_pkts}")
    print(f"[test_load] Total time    : {total_time:.1f}s")
    print(f"[test_load] Throughput    : {throughput:.0f} packets/s")
    print(f"[test_load] Avg per client: {sum(times)/len(times):.2f}s")
    assert failed == 0, f"{failed} client(s) failed"


if __name__ == "__main__":
    run()
