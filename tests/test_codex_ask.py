"""Runner contract: final answers only, safe failures, and image identity."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_ask as AI


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        binary = Path(self.tmp.name) / "codex"
        binary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
prompt = sys.stdin.read()
if os.environ.get("FAKE_RECORD"):
    pathlib.Path(os.environ["FAKE_RECORD"]).write_text(json.dumps({"args":args,"prompt":prompt}))
print("progress output that must never reach the answer")
print("diagnostic output", file=sys.stderr)
time.sleep(float(os.environ.get("FAKE_SLEEP", 0)))
pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(os.environ.get("FAKE_ANSWER", ""))
sys.exit(int(os.environ.get("FAKE_EXIT", 0)))
''')
        binary.chmod(0o755)
        env = patch.dict(os.environ, {"PATH": self.tmp.name + os.pathsep + os.environ["PATH"]})
        env.start()
        self.addCleanup(env.stop)

    def test_final_json_and_model_aliases(self):
        with patch.dict(os.environ, {"FAKE_ANSWER": '```json\n[{"n":2,"why":"oui"}]\n```',
                                     "BOW_CODEX_MODEL": "gpt-6.1-sol",
                                     "BOW_CODEX_EXTRACT_MODEL": "gpt-6-luna"}):
            self.assertEqual(json.loads(AI.ask("pick", AI.resolve_model("opus"), json_output=True)),
                             [{"n": 2, "why": "oui"}])
            self.assertEqual(AI.resolve_model("sonnet"), "gpt-6-luna")
            self.assertEqual(AI.resolve_model("gpt-6-astra"), "gpt-6-astra")

    def test_failed_invalid_empty_and_timed_out_answers_are_rejected(self):
        for answer, status in (("", "0"), ("not JSON", "0"), ('{"ok":true}', "1"), ("null", "0")):
            with self.subTest(answer=answer, status=status), patch.dict(os.environ, {
                    "FAKE_ANSWER": answer, "FAKE_EXIT": status}):
                with self.assertRaises(ValueError):
                    AI.ask("pick", "gpt-6.1-sol", json_output=True)
        with patch.dict(os.environ, {"FAKE_SLEEP": "5"}):
            with self.assertRaisesRegex(ValueError, "timed out"):
                AI.ask("pick", "gpt-6.1-sol", timeout=0.1)

    def test_photo_inputs_preserve_filename_identity_and_no_shell_tools(self):
        folder = Path(self.tmp.name) / "images"
        folder.mkdir()
        (folder / "candidates.json").write_text('{"soup-0.jpg":{},"soup-1.jpg":{}}')
        for name in ("soup-0.jpg", "soup-1.jpg"):
            (folder / name).write_bytes(b"test")
        record = Path(self.tmp.name) / "record.json"
        with patch.dict(os.environ, {"FAKE_ANSWER": '{"Soup":"soup-1.jpg"}', "FAKE_RECORD": str(record)}):
            self.assertEqual(json.loads(AI.ask("pick", "gpt-6.1-sol", images_dir=folder,
                                              json_output=True)), {"Soup": "soup-1.jpg"})
        invocation = json.loads(record.read_text())
        self.assertIn("1: soup-0.jpg\n2: soup-1.jpg", invocation["prompt"])
        self.assertEqual([a for a in invocation["args"] if a.startswith("--image=")],
                         [f"--image={folder.resolve() / name}" for name in ("soup-0.jpg", "soup-1.jpg")])
        self.assertIn("--ignore-user-config", invocation["args"])
        self.assertIn("read-only", invocation["args"])
        self.assertIn("shell_tool", invocation["args"])
        self.assertIn("multi_agent", invocation["args"])

    def test_photo_path_cannot_escape_candidates(self):
        folder = Path(self.tmp.name) / "images"
        folder.mkdir()
        (folder / "candidates.json").write_text('{"../codex":{}}')
        with self.assertRaisesRegex(ValueError, "invalid image"):
            AI.ask("pick", "gpt-6.1-sol", images_dir=folder)


if __name__ == "__main__":
    unittest.main()
