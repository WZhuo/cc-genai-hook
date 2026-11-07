import json
from argparse import Namespace

from cc_genai_hook.install import install, uninstall


def _args(tmp_path):
    return Namespace(
        mode="command",
        endpoint="http://127.0.0.1:4318",
        endpoint_kind="base",
        header=["x-api-key=test"],
        user="tester",
        content_mode="last_turn",
        dry_run=False,
        config=str(tmp_path / "genai-hook.json"),
        settings=str(tmp_path / "settings.json"),
    )


def _hooks(path):
    return json.loads(path.read_text())["hooks"]


def test_install_is_idempotent_and_preserves_other_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo existing",
                                }
                            ]
                        }
                    ],
                    "Notification": [
                        {"hooks": [{"type": "command", "command": "echo notify"}]}
                    ],
                }
            }
        )
    )

    assert install(_args(tmp_path)) == 0
    hooks = _hooks(settings)
    assert len(hooks["Stop"]) == 2
    assert len(hooks["SessionEnd"]) == 1
    assert len(hooks["PostToolUse"]) == 1
    assert len(hooks["PostToolUseFailure"]) == 1
    assert hooks["PostToolUse"][0]["matcher"] == "*"
    assert hooks["Notification"][0]["hooks"][0]["command"] == "echo notify"
    assert any(
        "cc_genai_hook hook stop" in handler["command"]
        for entry in hooks["Stop"]
        for handler in entry["hooks"]
    )

    assert install(_args(tmp_path)) == 0
    hooks = _hooks(settings)
    assert len(hooks["Stop"]) == 2
    assert len(hooks["SessionEnd"]) == 1
    assert len(hooks["PostToolUse"]) == 1
    assert len(hooks["PostToolUseFailure"]) == 1

    config = json.loads((tmp_path / "genai-hook.json").read_text())
    assert config["exporter"]["endpoint"] == "http://127.0.0.1:4318"
    assert config["exporter"]["headers"]["x-api-key"] == "test"
    assert config["resource"]["attributes"]["enduser.id"] == "tester"


def test_uninstall_removes_only_our_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "echo existing"}
                            ]
                        }
                    ]
                }
            }
        )
    )
    assert install(_args(tmp_path)) == 0
    assert uninstall(_args(tmp_path)) == 0
    hooks = _hooks(settings)
    assert hooks["Stop"] == [
        {"hooks": [{"type": "command", "command": "echo existing"}]}
    ]
    assert "SessionEnd" not in hooks
    assert "PostToolUse" not in hooks
    assert "PostToolUseFailure" not in hooks


def test_sidecar_mode_is_not_available_in_m1(tmp_path):
    args = _args(tmp_path)
    args.mode = "sidecar"
    assert install(args) == 2
