import os
import sys
import json
import time
import logging
from concurrent import futures

import grpc
import redis
from google.protobuf.json_format import MessageToDict

# Generated stubs live in ./generated/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated"))

import component_alignment_service_pb2
import component_alignment_service_pb2_grpc
import component_bonding_service_pb2
import component_bonding_service_pb2_grpc
import component_creation_service_pb2
import component_creation_service_pb2_grpc
import component_pbi_service_pb2
import component_pbi_service_pb2_grpc
import component_pickup_service_pb2
import component_pickup_service_pb2_grpc
import component_transfer_service_pb2
import component_transfer_service_pb2_grpc
import production_preproduction_service_pb2
import production_preproduction_service_pb2_grpc

GRPC_PORT   = os.getenv("GRPC_PORT",   "50051")
REDIS_HOST  = os.getenv("REDIS_HOST",  "localhost")
REDIS_PORT  = int(os.getenv("REDIS_PORT", "6379"))
STREAM_KEY  = os.getenv("STREAM_KEY",  "data_stream")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "50"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [server] %(message)s")
log = logging.getLogger(__name__)


def push(r: redis.Redis, service: str, message):
    """Serialize a protobuf Report to JSON and push onto the Redis stream."""
    data = MessageToDict(
        message,
        preserving_proto_field_name=True,
    )
    r.xadd(STREAM_KEY, {
        "service":     service,
        "received_at": str(int(time.time() * 1000)),
        "data":        json.dumps(data),
    })


# ── Alignment ──────────────────────────────────────────────────────────────

class AlignmentServicer(
    component_alignment_service_pb2_grpc.AlignmentDataServiceServicer
):
    def __init__(self, r): self.r = r

    def PushReport(self, request, context):
        push(self.r, "alignment", request)
        log.info("alignment  component=%-36s production=%s",
                 request.id.id, request.productionId.id)
        return component_alignment_service_pb2.ReportReply()


# ── Bonding ────────────────────────────────────────────────────────────────

class BondingServicer(
    component_bonding_service_pb2_grpc.BondingDataServiceServicer
):
    def __init__(self, r): self.r = r

    def PushReport(self, request, context):
        push(self.r, "bonding", request)
        log.info("bonding    component=%-36s production=%s",
                 request.id.id, request.productionId.id)
        return component_bonding_service_pb2.ReportReply()


# ── Creation ───────────────────────────────────────────────────────────────

class CreationServicer(
    component_creation_service_pb2_grpc.CreationDataServiceServicer
):
    def __init__(self, r): self.r = r

    def PushReport(self, request, context):
        push(self.r, "creation", request)
        log.info("creation   component=%-36s production=%s",
                 request.id.id, request.productionId.id)
        return component_creation_service_pb2.ReportReply()


# ── Post-Bond Inspection ───────────────────────────────────────────────────

class PbiServicer(
    component_pbi_service_pb2_grpc.PostBondInspectionDataServiceServicer
):
    def __init__(self, r): self.r = r

    def PushReport(self, request, context):
        push(self.r, "pbi", request)
        log.info("pbi        component=%-36s production=%s",
                 request.id.id, request.productionId.id)
        return component_pbi_service_pb2.ReportReply()


# ── Pickup ─────────────────────────────────────────────────────────────────

class PickupServicer(
    component_pickup_service_pb2_grpc.PickupDataServiceServicer
):
    def __init__(self, r): self.r = r

    def PushReport(self, request, context):
        push(self.r, "pickup", request)
        log.info("pickup     component=%-36s production=%s",
                 request.id.id, request.productionId.id)
        return component_pickup_service_pb2.ReportReply()


# ── Transfer ───────────────────────────────────────────────────────────────

class TransferServicer(
    component_transfer_service_pb2_grpc.TransferDataServiceServicer
):
    def __init__(self, r): self.r = r

    def PushReport(self, request, context):
        push(self.r, "transfer", request)
        log.info("transfer   component=%-36s production=%s",
                 request.id.id, request.productionId.id)
        return component_transfer_service_pb2.ReportReply()


# ── Pre-Production ─────────────────────────────────────────────────────────

class PreproductionServicer(
    production_preproduction_service_pb2_grpc.PreproductionDataServiceServicer
):
    def __init__(self, r): self.r = r

    def PushReport(self, request, context):
        push(self.r, "preproduction", request)
        log.info("preproduction production=%-36s machine=%s",
                 request.id.id, request.machineName.value)
        return production_preproduction_service_pb2.ReportReply()


# ── Server bootstrap ───────────────────────────────────────────────────────

def serve():
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))

    component_alignment_service_pb2_grpc.add_AlignmentDataServiceServicer_to_server(
        AlignmentServicer(r), server)
    component_bonding_service_pb2_grpc.add_BondingDataServiceServicer_to_server(
        BondingServicer(r), server)
    component_creation_service_pb2_grpc.add_CreationDataServiceServicer_to_server(
        CreationServicer(r), server)
    component_pbi_service_pb2_grpc.add_PostBondInspectionDataServiceServicer_to_server(
        PbiServicer(r), server)
    component_pickup_service_pb2_grpc.add_PickupDataServiceServicer_to_server(
        PickupServicer(r), server)
    component_transfer_service_pb2_grpc.add_TransferDataServiceServicer_to_server(
        TransferServicer(r), server)
    production_preproduction_service_pb2_grpc.add_PreproductionDataServiceServicer_to_server(
        PreproductionServicer(r), server)

    server.add_insecure_port(f"0.0.0.0:{GRPC_PORT}")
    server.start()
    log.info("gRPC server on :%s — 7 services registered, %d workers",
             GRPC_PORT, MAX_WORKERS)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
