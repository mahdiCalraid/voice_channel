import unittest
import os
import json
import shutil
import tempfile
from unittest.mock import patch, MagicMock

# Add parent directory to path so we can import workers
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

class TestWorkerRunner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)
        
    def test_registry_loading(self):
        registry_path = os.path.join(os.path.dirname(__file__), "../workers/registry.json")
        self.assertTrue(os.path.exists(registry_path))
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
        self.assertIn("openai", registry)
        self.assertIn("agy", registry)
        self.assertIn("codex", registry)
        self.assertEqual(registry["openai"]["adapter"], "openai")
        self.assertEqual(registry["agy"]["adapter"], "agy")
        self.assertEqual(registry["codex"]["adapter"], "codex")

    @patch("workers.providers.openai_provider.OpenAIProvider.execute")
    def test_run_worker_digest_openai(self, mock_openai_execute):
        mock_openai_execute.return_value = "This is a mock OpenAI summary."
        
        # Create input files in temp job directory
        job_dir = os.path.join(self.temp_dir, "job_1")
        os.makedirs(job_dir, exist_ok=True)
        
        # Write inputs
        messages = [
            {"id": "m1", "lane": "agent", "text": "Task finished", "event": {"agent": "codex"}},
            {"id": "m2", "lane": "user", "text": "What next?", "username": "ed"}
        ]
        with open(os.path.join(job_dir, "messages.json"), "w") as f:
            json.dump(messages, f)
            
        system_events = [
            {"event": {"kind": "routing", "agent": "codex", "status": "completed", "response_time_seconds": 45}}
        ]
        with open(os.path.join(job_dir, "system_events.json"), "w") as f:
            json.dump(system_events, f)
            
        with open(os.path.join(job_dir, "matter.md"), "w") as f:
            f.write("Project objectives content")
            
        with open(os.path.join(job_dir, "instructions.md"), "w") as f:
            f.write("Special rules here")
            
        room_context = {"prior_summaries": [{"timestamp": "2026-07-17", "digest": "Old digest"}]}
        with open(os.path.join(job_dir, "room.json"), "w") as f:
            json.dump(room_context, f)
            
        # Write job.json
        job_cfg = {
            "room_id": "test_room",
            "model": "gpt-4o-mini",
            "effort": "low",
            "input_files": {
                "messages": "messages.json",
                "system_events": "system_events.json",
                "matter_context": "matter.md",
                "task_instructions": "instructions.md",
                "room_context": "room.json"
            }
        }
        job_path = os.path.join(job_dir, "job.json")
        with open(job_path, "w") as f:
            json.dump(job_cfg, f)
            
        # Invoke run_worker main
        from workers.run_worker import main
        
        # We patch sys.argv to simulate running the script
        test_argv = ["run_worker.py", "digest", "--worker", "openai", "--job", job_path]
        with patch.object(sys, "argv", test_argv):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            
        # Verify result.json is written
        res_path = os.path.join(job_dir, "result.json")
        self.assertTrue(os.path.exists(res_path))
        with open(res_path, "r") as f:
            res = json.load(f)
            
        self.assertTrue(res["ok"])
        self.assertEqual(res["task"], "digest")
        self.assertEqual(res["worker"], "openai")
        self.assertEqual(res["output"], "This is a mock OpenAI summary.")
        self.assertEqual(res["included_message_ids"], ["m1", "m2"])
        
    @patch("subprocess.run")
    @patch("shutil.which")
    def test_run_worker_digest_agy_cli(self, mock_which, mock_run):
        mock_which.return_value = "/Users/ed/.local/bin/agy"
        
        # Mock subprocess call
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "This is a mock agy CLI summary."
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc
        
        job_dir = os.path.join(self.temp_dir, "job_2")
        os.makedirs(job_dir, exist_ok=True)
        
        # Write files
        with open(os.path.join(job_dir, "messages.json"), "w") as f:
            json.dump([], f)
        job_cfg = {
            "room_id": "test_room",
            "input_files": {
                "messages": "messages.json"
            }
        }
        job_path = os.path.join(job_dir, "job.json")
        with open(job_path, "w") as f:
            json.dump(job_cfg, f)
            
        from workers.run_worker import main
        test_argv = ["run_worker.py", "digest", "--worker", "agy", "--job", job_path]
        with patch.object(sys, "argv", test_argv):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)
            
        # Verify result.json
        res_path = os.path.join(job_dir, "result.json")
        self.assertTrue(os.path.exists(res_path))
        with open(res_path, "r") as f:
            res = json.load(f)
            
        self.assertTrue(res["ok"])
        self.assertEqual(res["worker"], "agy")
        self.assertEqual(res["output"], "This is a mock agy CLI summary.")

    @patch("workers.providers.codex_provider.subprocess.run")
    @patch("workers.providers.codex_provider.shutil.which")
    def test_run_worker_digest_codex_cli(self, mock_which, mock_run):
        mock_which.return_value = "/usr/local/bin/codex"

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "\n".join([
            json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "message": "Codex digest output"
                }
            }),
            json.dumps({
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": "Codex digest output"
                }
            }),
        ])
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        job_dir = os.path.join(self.temp_dir, "job_3")
        os.makedirs(job_dir, exist_ok=True)

        with open(os.path.join(job_dir, "messages.json"), "w") as f:
            json.dump([{"id": "m1", "lane": "agent", "text": "done", "event": {"agent": "codex"}}], f)
        with open(os.path.join(job_dir, "system_events.json"), "w") as f:
            json.dump([], f)
        with open(os.path.join(job_dir, "matter.md"), "w") as f:
            f.write("NORTH_STAR.md")
        with open(os.path.join(job_dir, "instructions.md"), "w") as f:
            f.write("Keep it short.")
        with open(os.path.join(job_dir, "room.json"), "w") as f:
            json.dump({"prior_summaries": []}, f)

        job_cfg = {
            "room_id": "test_room",
            "model": "gpt-5.6-terra",
            "input_files": {
                "messages": "messages.json",
                "system_events": "system_events.json",
                "matter_context": "matter.md",
                "task_instructions": "instructions.md",
                "room_context": "room.json"
            }
        }
        job_path = os.path.join(job_dir, "job.json")
        with open(job_path, "w") as f:
            json.dump(job_cfg, f)

        from workers.run_worker import main
        test_argv = ["run_worker.py", "digest", "--worker", "codex", "--job", job_path]
        with patch.object(sys, "argv", test_argv):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)

        res_path = os.path.join(job_dir, "result.json")
        self.assertTrue(os.path.exists(res_path))
        with open(res_path, "r") as f:
            res = json.load(f)

        self.assertTrue(res["ok"])
        self.assertEqual(res["worker"], "codex")
        self.assertEqual(res["output"], "Codex digest output")

if __name__ == '__main__':
    unittest.main()
