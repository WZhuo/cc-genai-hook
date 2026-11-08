"""Install/uninstall the command-mode hooks."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shlex
import shutil
import sys
from typing import Any, Dict, List, Optional

from .config import (
    DEFAULTS,
    _deep_merge,
    default_config_path,
    headers_from_pairs,
    write_config,
)

HOOK_MARKERS = ("cc_genai_hook", "cc-genai-hook")


def _settings_path(args: argparse.Namespace) -> str:
    return os.path.expanduser(
        getattr(args, "settings", None) or "~/.claude/settings.json"
    )


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise SystemExit("invalid JSON in %s: %s" % (path, exc))


def _backup(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = "%s.bak.%s" % (path, stamp)
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    return backup


def _write_settings(path: str, settings: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _hook_command(event: str) -> str:
    return "%s -m cc_genai_hook hook %s" % (
        shlex.quote(sys.executable),
        event,
    )


def _entry(
    event: str, timeout: int, matcher: Optional[str] = None
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "hooks": [
            {
                "type": "command",
                "command": _hook_command(event),
                "timeout": timeout,
            }
        ]
    }
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def _is_ours(entry: Dict[str, Any]) -> bool:
    for handler in entry.get("hooks") or []:
        if not isinstance(handler, dict):
            continue
        command = str(handler.get("command", ""))
        if any(marker in command for marker in HOOK_MARKERS):
            return True
    return False


def _strip_ours(entries: List[Any]) -> List[Any]:
    return [
        entry
        for entry in entries
        if not (isinstance(entry, dict) and _is_ours(entry))
    ]


def _clean_config_for_write(cfg: Dict[str, Any]) -> Dict[str, Any]:
    clean = json.loads(json.dumps(cfg))
    exporter = clean.setdefault("exporter", {})
    for key in ("resolved_endpoint", "resolved_headers", "traces_endpoint"):
        exporter.pop(key, None)
    resource = clean.setdefault("resource", {})
    resource.pop("resolved_attributes", None)
    clean.pop("_config_path", None)
    return clean


def _build_config(existing: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    cfg = _deep_merge(DEFAULTS, existing)
    exporter = cfg.setdefault("exporter", {})

    if getattr(args, "endpoint", None):
        exporter["endpoint"] = args.endpoint
    if getattr(args, "endpoint_kind", None):
        exporter["endpoint_kind"] = args.endpoint_kind
    headers = headers_from_pairs(getattr(args, "header", None))
    if headers:
        merged = dict(exporter.get("headers") or {})
        merged.update(headers)
        exporter["headers"] = merged

    exporter["protocol"] = "http/protobuf"
    exporter.setdefault("compression", "gzip")
    exporter.setdefault("timeout_ms", 30000)
    exporter.setdefault("batch", {"max_size": 512, "schedule_delay_ms": 500})

    resource = cfg.setdefault("resource", {})
    resource.setdefault("service.name", "claude-code")
    attrs = dict(resource.get("attributes") or {})
    if getattr(args, "user", None):
        attrs["enduser.id"] = args.user
    resource["attributes"] = attrs

    if getattr(args, "content_mode", None):
        cfg.setdefault("content", {})["mode"] = args.content_mode
    cfg.setdefault("content", {}).setdefault("mode", "last_turn")
    cfg.setdefault("content", {}).setdefault("include_thinking", False)
    cfg.setdefault("hook", {}).setdefault("agent_name", "claude-code")
    cfg["hook"]["tool_mode"] = getattr(args, "tool_mode", "ordered") or "ordered"
    cfg["enabled"] = True
    return cfg


def install(args: argparse.Namespace) -> int:
    mode = getattr(args, "mode", "command") or "command"
    if mode == "auto":
        mode = "command"
    if mode != "command":
        print(
            "error: only --mode command is implemented; sidecar mode is planned for M4",
            file=sys.stderr,
        )
        return 2

    config_path = os.path.expanduser(
        getattr(args, "config", None) or default_config_path()
    )
    existing = _read_json(config_path) if os.path.exists(config_path) else {}
    cfg = _build_config(existing, args)

    settings_path = _settings_path(args)
    settings = _read_json(settings_path)
    hooks = dict(settings.get("hooks") or {})
    hooks["Stop"] = _strip_ours(list(hooks.get("Stop") or [])) + [
        _entry("stop", 60)
    ]
    hooks["SessionEnd"] = _strip_ours(list(hooks.get("SessionEnd") or [])) + [
        _entry("session_end", 30)
    ]
    tool_mode = getattr(args, "tool_mode", "ordered") or "ordered"
    if tool_mode == "realtime":
        hooks["PostToolUse"] = _strip_ours(
            list(hooks.get("PostToolUse") or [])
        ) + [_entry("post_tool_use", 30, "*")]
        hooks["PostToolUseFailure"] = _strip_ours(
            list(hooks.get("PostToolUseFailure") or [])
        ) + [_entry("post_tool_use_failure", 30, "*")]
    else:
        for event in ("PostToolUse", "PostToolUseFailure"):
            kept = _strip_ours(list(hooks.get(event) or []))
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)

    # M3 handles subagents from the parent Stop; stale standalone
    # SubagentStop entries from older installs are removed.
    kept = _strip_ours(list(hooks.get("SubagentStop") or []))
    if kept:
        hooks["SubagentStop"] = kept
    else:
        hooks.pop("SubagentStop", None)
    settings["hooks"] = hooks

    if getattr(args, "dry_run", False):
        print("config: %s" % config_path)
        print(json.dumps(_clean_config_for_write(cfg), ensure_ascii=False, indent=2))
        print("settings: %s" % settings_path)
        print(json.dumps({"hooks": hooks}, ensure_ascii=False, indent=2))
        return 0

    config_backup = _backup(config_path)
    backup = _backup(settings_path)
    write_config(config_path, _clean_config_for_write(cfg))
    _write_settings(settings_path, settings)
    print("[ok] config written: %s" % config_path)
    if config_backup:
        print("[ok] config backup: %s" % config_backup)
    if backup:
        print("[ok] settings backup: %s" % backup)
    installed = "Stop, SessionEnd"
    if tool_mode == "realtime":
        installed += ", PostToolUse, PostToolUseFailure"
    print("[ok] hooks installed: %s (tool_mode=%s)" % (installed, tool_mode))
    print("[next] run: cc-genai-hook doctor --send-test")
    return 0


def uninstall(args: argparse.Namespace) -> int:
    settings_path = _settings_path(args)
    settings = _read_json(settings_path)
    hooks = dict(settings.get("hooks") or {})
    removed = 0
    for event in (
        "Stop",
        "SessionEnd",
        "SubagentStop",
        "PostToolUse",
        "PostToolUseFailure",
    ):
        entries = list(hooks.get(event) or [])
        kept = _strip_ours(entries)
        removed += len(entries) - len(kept)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    settings["hooks"] = hooks

    if not getattr(args, "dry_run", False):
        backup = _backup(settings_path)
        _write_settings(settings_path, settings)
        if backup:
            print("[ok] settings backup: %s" % backup)
    print("[ok] removed %d hook entr%s" % (removed, "y" if removed == 1 else "ies"))

    if getattr(args, "purge", False):
        config_path = os.path.expanduser(
            getattr(args, "config", None) or default_config_path()
        )
        state_dir = os.path.expanduser("~/.claude/state/genai-hook")
        for path in (config_path, state_dir):
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
                print("[ok] removed %s" % path)
            elif os.path.exists(path):
                os.remove(path)
                print("[ok] removed %s" % path)
    return 0


def hook_entries(settings_path: Optional[str] = None) -> Dict[str, int]:
    """Return a count of our installed hooks per event, for doctor."""

    path = os.path.expanduser(settings_path or "~/.claude/settings.json")
    settings = _read_json(path)
    hooks = settings.get("hooks") or {}
    result: Dict[str, int] = {}
    for event, entries in hooks.items():
        count = 0
        for entry in entries or []:
            if isinstance(entry, dict) and _is_ours(entry):
                count += 1
        if count:
            result[event] = count
    return result
