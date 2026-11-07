#!/usr/bin/env python3
"""Minimal local OTLP/HTTP trace receiver for verification.

Usage:
    python3 examples/local_otlp_receiver.py --port 4318

It prints one compact JSON object per span, including trace/span/parent IDs,
attributes and span events.  It understands gzip-compressed OTLP bodies.
"""

from __future__ import annotations

import argparse
import gzip
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)


def _attr(value):
    kind = value.WhichOneof("value")
    if kind == "string_value":
        return value.string_value
    if kind == "int_value":
        return value.int_value
    if kind == "double_value":
        return value.double_value
    if kind == "bool_value":
        return value.bool_value
    if kind == "array_value":
        return [_attr(item) for item in value.array_value.values]
    if kind == "kvlist_value":
        return {item.key: _attr(item.value) for item in value.kvlist_value.values}
    return None


def _attrs(items):
    return {item.key: _attr(item.value) for item in items}


def _hex(value: bytes) -> str:
    return value.hex() if value else ""


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        if self.headers.get("content-encoding", "").lower() == "gzip":
            body = gzip.decompress(body)

        if self.path.rstrip("/").endswith("/v1/traces"):
            try:
                request = ExportTraceServiceRequest()
                request.ParseFromString(body)
                for resource_spans in request.resource_spans:
                    for scope_spans in resource_spans.scope_spans:
                        for span in scope_spans.spans:
                            output = {
                                "name": span.name,
                                "trace_id": _hex(span.trace_id),
                                "span_id": _hex(span.span_id),
                                "parent_span_id": _hex(span.parent_span_id),
                                "attrs": _attrs(span.attributes),
                                "events": [
                                    {
                                        "name": event.name,
                                        "attrs": _attrs(event.attributes),
                                    }
                                    for event in span.events
                                ],
                            }
                            print(json.dumps(output, ensure_ascii=False), flush=True)
            except Exception as exc:  # noqa: BLE001
                print(
                    json.dumps({"error": repr(exc), "bytes": len(body)}),
                    flush=True,
                )

        self.send_response(200)
        self.send_header("content-type", "application/x-protobuf")
        self.end_headers()
        self.wfile.write(b"")

    def log_message(self, fmt, *args):
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4318)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("listening on http://%s:%d" % (args.host, args.port), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
