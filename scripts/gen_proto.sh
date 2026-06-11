#!/usr/bin/env bash
# 由 proto/line_traffic.proto 重新生成 amap_service/publish/proto/line_traffic_pb2.py。
# 改了 .proto 后必须重跑本脚本。需先安装 dev 依赖(含 grpcio-tools)。
set -euo pipefail
cd "$(dirname "$0")/.."
python -m grpc_tools.protoc -I proto \
  --python_out=amap_service/publish/proto \
  proto/line_traffic.proto
echo "generated amap_service/publish/proto/line_traffic_pb2.py"
