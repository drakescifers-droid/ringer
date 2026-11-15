#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402
from ringer import (  # noqa: E402
    PersistentHudServer,
    apply_roster_action,
    load_roster,
    render_status_html,
    save_roster,
    search_catalog_models,
)


class RosterHudTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_env = os.environ.copy()
        self.addCleanup(self.restore_env)
        self.root = Path(self.tmp.name)
        os.environ["HOME"] = str(self.root / "home")
        os.environ["RINGER_HOME"] = str(self.root / "ringer-home")
        self.state_dir = self.root / "state"
        self.state_dir.mkdir(parents=True)
        Path(os.environ["RINGER_HOME"]).mkdir(parents=True)
        catalog = {
            "fetched_at": "2026-11-15T00:00:00+00:00",
            "models": [
                {
                    "id": "z-ai/glm-5.2",
                    "name": "GLM 5.2",
                    "free": False,
                    "context_length": 128000,
                    "prompt_per_m": 0.74,
                    "completion_per_m": 2.33,
                }
            ],
        }
        (Path(os.environ["RINGER_HOME"]) / "openrouter-catalog.json").write_text(
            json.dumps(catalog),
            encoding="utf-8",
        )

    def restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)

    def test_roster_round_trip_and_default_mark(self) -> None:
        save_roster(
            {
                "harnesses": {"opencode": {"enabled": True, "default_model": ""}},
                "models": [{"engine": "opencode", "model": "openrouter/other", "mark": "default"}],
            }
        )
        result = apply_roster_action(
            {"action": "set-default", "engine": "opencode", "model": "openrouter/z-ai/glm-5.2"}
        )
        self.assertTrue(result["ok"])
        stored = load_roster()
        marks = {item["model"]: item["mark"] for item in stored["models"]}
        self.assertEqual("default", marks["openrouter/z-ai/glm-5.2"])
        self.assertEqual("available", marks["openrouter/other"])
        self.assertEqual("openrouter/z-ai/glm-5.2", stored["harnesses"]["opencode"]["default_model"])
        shown = next(item for item in result["models"] if item["model"] == "openrouter/z-ai/glm-5.2")
        self.assertIn("0.74", shown["price"])
        self.assertIn('"engine": "opencode"', shown["snippet"])

    def test_catalog_search_requires_two_characters(self) -> None:
        self.assertEqual([], search_catalog_models("g"))
        matches = search_catalog_models("glm")
        self.assertEqual(["z-ai/glm-5.2"], [item["id"] for item in matches])
        self.assertTrue(matches[0]["snippet_model"].startswith("openrouter/"))

    def test_hud_roster_and_command_endpoints(self) -> None:
        runs = self.state_dir / "runs"
        runs.mkdir()
        (runs / "run-1.json").write_text(
            json.dumps(
                {
                    "run_id": "run-1",
                    "run_name": "demo",
                    "manifest_path": "/tmp/demo manifest.json",
                }
            ),
            encoding="utf-8",
        )
        server = PersistentHudServer(self.state_dir, preferred_port=0, open_viewer=False)
        port = server.start_background()
        self.addCleanup(server.stop)
        base = f"http://127.0.0.1:{port}"

        with urlopen(f"{base}/api/roster", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertIn("codex", [item["name"] for item in payload["harnesses"]])

        request = Request(
            f"{base}/api/roster",
            data=json.dumps(
                {"action": "add", "engine": "opencode", "model": "openrouter/z-ai/glm-5.2"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            added = json.loads(response.read().decode("utf-8"))
        self.assertTrue(any(item["model"] == "openrouter/z-ai/glm-5.2" for item in added["models"]))

        with urlopen(f"{base}/api/runs/command?run=run-1", timeout=5) as response:
            command = json.loads(response.read().decode("utf-8"))
        self.assertEqual('./ringer.py run \'/tmp/demo manifest.json\'', command["command"])

        with urlopen(f"{base}/", timeout=5) as response:
            page = response.read().decode("utf-8")
        self.assertIn('id="workers-tab"', page)
        self.assertIn('id="workers-panel"', page)
        self.assertIn('id="artifact-search"', page)

    def test_live_artifact_includes_worker_log(self) -> None:
        html = render_status_html(
            {
                "run_id": "run-1",
                "run_name": "demo",
                "identity": "tester",
                "state": "live",
                "elapsed_s": 12,
                "tasks": [
                    {
                        "key": "alpha",
                        "status": "running",
                        "spec": "Write the notes.",
                        "check": "test -s notes.md",
                        "log_tail_full": ["reading the spec", "\x1b[31mstill working\x1b[0m"],
                    }
                ],
            }
        )
        self.assertIn("reading the spec", html)
        self.assertIn("still working", html)
        self.assertNotIn("[31m", html)
        self.assertIn("What it was asked to do", html)
        self.assertIn("Write the notes.", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
