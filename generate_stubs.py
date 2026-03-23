#!/usr/bin/env python3
"""
generate_stubs.py  —  Compile all BESI proto files to Python gRPC stubs.

Usage:
    python generate_stubs.py

Output goes to ./generated/
All seven service directories are compiled into a single flat output directory.
Shared type files (base-types, automation-types, etc.) are identical across
service directories and produce identical Python output; later writes overwrite
earlier ones without any loss.
"""
import sys
from pathlib import Path


def compile_dir(include_dir: Path, output_dir: Path):
    import os as _os
    import grpc_tools as _gt
    from grpc_tools import protoc

    # grpcio-tools ships google/protobuf/*.proto — add that include path
    proto_include = _os.path.join(_os.path.dirname(_gt.__file__), "_proto")

    proto_files = sorted(include_dir.glob("*.proto"))
    if not proto_files:
        print(f"  WARNING: no .proto files in {include_dir}")
        return

    args = [
        "grpc_tools.protoc",
        f"-I{include_dir}",
        f"-I{proto_include}",
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}",
    ] + [str(f) for f in proto_files]

    rc = protoc.main(args)
    if rc != 0:
        print(f"  ERROR compiling {include_dir}")
        sys.exit(1)
    print(f"  OK  {include_dir.name}  ({len(proto_files)} files)")


def main():
    root   = Path(__file__).parent.resolve()
    proto  = root / "proto"
    output = root / "generated"
    output.mkdir(exist_ok=True)

    services = [
        proto / "component_data_collection" / "alignment",
        proto / "component_data_collection" / "bonding",
        proto / "component_data_collection" / "creation",
        proto / "component_data_collection" / "pbi",
        proto / "component_data_collection" / "pickup",
        proto / "component_data_collection" / "transfer",
        proto / "production_data_collection" / "pre-production",
    ]

    print("Compiling BESI proto files ...")
    for svc_dir in services:
        compile_dir(svc_dir, output)

    generated = list(output.glob("*.py"))
    print(f"\nDone — {len(generated)} Python files in {output}")


if __name__ == "__main__":
    main()
