"""Offline mobile/desktop integration checks; no live account or cloud calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from core.mobile_bridge import pairing_payload, record_workers
from core.cloud_bridge import CloudBridge, BridgeConfig
from memory import cloud_safe, memory_manager


def fact(value):
    return {"category": "notes", "key": "project", "value": value}

class MobileChecks(unittest.TestCase):
    def test_cloud_memory_three_way_merge(self):
        base = {"notes/project": fact("one")}
        merged, conflicts = cloud_safe.merge_facts(base, base, {"notes/project": fact("two")})
        self.assertEqual(merged["notes/project"]["value"], "two")
        self.assertEqual(conflicts, [])
        merged, conflicts = cloud_safe.merge_facts(base, {"notes/project": fact("ours")}, {"notes/project": fact("theirs")})
        self.assertEqual(conflicts, ["notes/project"])
        self.assertEqual(merged["notes/project"]["value"], "theirs")

    def test_cloud_memory_atomic_revision_and_secret_rejection(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cloud_safe, "PATH", Path(folder) / "memory.json"):
            cloud_safe.upsert("notes", "project", "one")
            self.assertFalse(cloud_safe.accept_cloud({"notes/project": fact("two")}, 0))
            self.assertTrue(cloud_safe.accept_cloud({"notes/project": fact("two")}, 1))
            self.assertIn("two", cloud_safe.prompt_context())
            with self.assertRaises(ValueError):
                cloud_safe.accept_cloud({"notes/project": fact("password=private")}, 2)

    def test_long_term_projection_and_cloud_import(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cloud_safe, "PATH", Path(folder) / "cloud_safe.json"), patch.object(memory_manager, "MEMORY_PATH", Path(folder) / "long_term.json"):
            memory_manager.update_memory({"identity": {"name": {"value": "Ryon"}}, "relationships": {"friend": {"value": "Ari"}}})
            projected = cloud_safe.project_long_term()
            self.assertEqual(projected["facts"]["identity/name"]["value"], "Ryon")
            cloud_safe.apply_to_long_term({"projects/mobile": {"category": "projects", "key": "mobile", "value": "ArienX Mobile"}})
            self.assertEqual(memory_manager.load_memory()["projects"]["mobile"]["value"], "ArienX Mobile")

    def test_pairing_validation_never_accepts_credentials(self):
        query = urlencode({"cloud": "https://cloud.example", "code": "a" * 64})
        self.assertEqual(pairing_payload({"qr": "arienx://pair?" + query}), "arienx://pair?" + query)
        for cloud in ("http://cloud.example", "https://user:secret@cloud.example"):
            with self.assertRaises(ValueError):
                pairing_payload({"qr": "arienx://pair?" + urlencode({"cloud": cloud, "code": "a" * 64})})

    def test_worker_snapshot_omits_private_results(self):
        import core.mobile_bridge as mobile
        record_workers({"workers": [{"id": "one", "heading": "private user prompt", "diagnostic": "secret result", "model": "test-model", "status": "Running"}]})
        text = json.dumps(mobile._workers)
        self.assertNotIn("private", text)
        self.assertNotIn("secret", text)
        self.assertIn("Running", text)

    def test_telemetry_requires_explicit_opt_in(self):
        cfg = BridgeConfig(url="https://cloud.example", device_id="a"*32, private_key_pem="not-a-real-key", enabled=True)
        bridge = CloudBridge(cfg)
        with patch("core.cloud_bridge.get_config", return_value=cfg), patch("core.cloud_bridge.requests.post") as post:
            self.assertFalse(bridge.publish_mobile_snapshot())
            post.assert_not_called()

    def test_remote_command_allowlist(self):
        bridge = CloudBridge(BridgeConfig())
        self.assertIsNone(bridge.queue_task("execute_shell", {"command": "anything"}))
        self.assertIsNone(bridge.queue_task("read_private_key", {}))

if __name__ == "__main__":
    unittest.main()
