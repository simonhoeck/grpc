"""
producer.py — läuft auf der Anlage.

Konfiguration via Umgebungsvariablen (oder .env):
  GRPC_SERVER   IP:Port des Servers  (default: localhost:50051)
  DEVICE_ID     eindeutiger Name der Anlage (default: anlage-001)
  INTERVAL      Messintervall in Sekunden   (default: 1.0)
"""
import os
import json
import time
import logging

import grpc
import data_pb2
import data_pb2_grpc

GRPC_SERVER = os.getenv("GRPC_SERVER", "localhost:50051")
DEVICE_ID   = os.getenv("DEVICE_ID",   "anlage-001")
INTERVAL    = float(os.getenv("INTERVAL", "1.0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [producer] %(message)s")
log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  HIER ANPASSEN: echte Sensorwerte lesen                             #
# ------------------------------------------------------------------ #
def read_sensors() -> dict:
    """
    Gibt ein dict mit aktuellen Messwerten zurück.
    Ersetze die Beispielwerte mit deinen echten Quellen,
    z.B. Modbus, OPC-UA, GPIO, Serial, ...
    """
    return {
        "temperature": 0.0,   # °C
        "pressure":    0.0,   # bar
        "rpm":         0,     # U/min
        # weitere Felder ergänzen ...
    }


def read_events() -> list[dict] | None:
    """
    Optional: gibt eine Liste von Events zurück wenn etwas passiert ist,
    sonst None. Beispiel: Alarm, Statuswechsel, Fehlercode.
    """
    return None
# ------------------------------------------------------------------ #


def packet_stream():
    """Endlosschleife: liest Sensoren und erzeugt DataPackets."""
    while True:
        ts = int(time.time() * 1000)

        # Sensor-Paket
        data = read_sensors()
        yield data_pb2.DataPacket(
            type="sensor",
            timestamp=ts,
            device_id=DEVICE_ID,
            payload=json.dumps(data).encode(),
        )

        # Event-Pakete (falls vorhanden)
        events = read_events()
        if events:
            for event in events:
                yield data_pb2.DataPacket(
                    type="event",
                    timestamp=ts,
                    device_id=DEVICE_ID,
                    payload=json.dumps(event).encode(),
                )

        time.sleep(INTERVAL)


def run():
    log.info("Verbinde mit %s als '%s'", GRPC_SERVER, DEVICE_ID)
    while True:
        try:
            with grpc.insecure_channel(GRPC_SERVER) as channel:
                stub = data_pb2_grpc.DataServiceStub(channel)
                ack = stub.SendData(packet_stream())
                log.info("Stream beendet — ack=%s", ack.success)
        except grpc.RpcError as e:
            log.error("Verbindung verloren: %s — reconnect in 5s", e.details())
            time.sleep(5)


if __name__ == "__main__":
    run()
