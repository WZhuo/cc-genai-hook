"""Health checks and config inspection."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from typing import Any, Dict, List

from .config import ConfigError, load_config, redact_headers
from .emitter import build_tracer, emit_test_span, flush
from .install import hook_entries
from .state import StateStore


def _sanitize(cfg: Dict[str, Any], redact: bool) -> Dict[str, Any]:
    clean = json.loads(json.dumps(cfg))
    clean.pop("_config_path", None)
    exporter = clean.get("exporter") or {}
    exporter.pop("resolved_endpoint", None)
    exporter.pop("resolved_headers", None)
    if redact:
        exporter["headers"] = redact_headers(exporter.get("headers") or {})
    resource = clean.get("resource") or {}
    resource.pop("resolved_attributes", None)
    return clean


def show_config(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(
            getattr(args, "config", None), require_endpoint=False
        )
    except ConfigError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(_sanitize(cfg, getattr(args, "redact", False)), indent=2, ensure_ascii=False))
    return 0


def validate_config(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(getattr(args, "config", None), require_endpoint=True)
    except ConfigError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print("[ok] config valid: %s" % cfg.get("_config_path"))
    print("[ok] endpoint: %s" % cfg["exporter"]["resolved_endpoint"])
    print("[ok] protocol: %s" % cfg["exporter"]["protocol"])
    print("[ok] headers: %s" % ", ".join(sorted(cfg["exporter"]["resolved_headers"])))
    return 0


def doctor(args: argparse.Namespace) -> int:
    errors: List[str] = []
    warnings: List[str] = []
    try:
        cfg = load_config(getattr(args, "config", None), require_endpoint=False)
    except ConfigError as exc:
        print("[error] config: %s" % exc, file=sys.stderr)
        return 1

    dry_run = bool(cfg.get("hook", {}).get("dry_run", False))
    if not dry_run:
        try:
            cfg = load_config(getattr(args, "config", None), require_endpoint=True)
        except ConfigError as exc:
            errors.append(str(exc))

    print("config:   %s" % cfg.get("_config_path"))
    print("enabled:  %s" % cfg.get("enabled"))
    print("dry_run:  %s" % dry_run)
    print("endpoint: %s" % (cfg["exporter"].get("resolved_endpoint") or "(not set)"))
    print("protocol: %s" % cfg["exporter"].get("protocol"))
    print("headers:  %s" % (", ".join(sorted(cfg["exporter"].get("resolved_headers") or {})) or "(none)"))
    print("state:    %s" % cfg["state"]["dir"])

    for module in (
        "opentelemetry.sdk",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    ):
        try:
            importlib.import_module(module)
            print("[ok] dependency: %s" % module)
        except Exception as exc:
            errors.append("missing dependency %s: %s" % (module, exc))
            print("[error] dependency: %s (%s)" % (module, exc))

    try:
        os.makedirs(cfg["state"]["dir"], exist_ok=True)
        store = StateStore(cfg["state"]["dir"])
        try:
            healthy = store.health()
            counts = store.counts()
            tools = store.tool_count()
        finally:
            store.close()
        if healthy:
            print(
                "[ok] state: healthy (sessions=%d segments=%d messages=%d tools=%d)"
                % (counts + (tools,))
            )
        else:
            errors.append("state database is not healthy")
    except Exception as exc:
        errors.append("state error: %s" % exc)
        print("[error] state: %s" % exc)

    installed = hook_entries(getattr(args, "settings", None))
    if installed:
        print("[ok] hooks: %s" % ", ".join("%s(%d)" % item for item in sorted(installed.items())))
    else:
        warnings.append("no cc-genai-hook hooks found in settings")
        print("[warn] hooks: none installed")

    for warning in warnings:
        print("[warn] %s" % warning)

    if getattr(args, "send_test", False):
        if not dry_run and not cfg["exporter"].get("resolved_endpoint"):
            errors.append("cannot send test span without an endpoint")
        else:
            try:
                bundle = build_tracer(cfg)
                emit_test_span(bundle)
                ok = flush(bundle)
                if ok:
                    print("[ok] test span sent")
                else:
                    errors.append("test span export failed")
                    print("[error] test span export failed")
            except Exception as exc:
                errors.append("test span failed: %s" % exc)
                print("[error] test span failed: %s" % exc)

    if errors:
        for error in errors:
            print("[error] %s" % error, file=sys.stderr)
        return 1
    return 0
