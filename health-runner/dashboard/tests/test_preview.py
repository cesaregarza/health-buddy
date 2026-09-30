import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dashboard_fixture import AS_OF, snapshot
from scripts.preview import render, refresh_training

ROOT = Path(__file__).resolve().parents[1]


class PreviewTests(unittest.TestCase):
    def test_real_template_serializes_script_like_text_safely(self):
        data = snapshot()
        data["visit"]["note_markdown"] = "</script><script>throw Error('example')</script>"
        html = render(copy.deepcopy(data), AS_OF)
        self.assertEqual(html.count("</script>"), 1)
        self.assertNotIn("/*__DATA__*/", html)

    def test_cli_defaults_to_synthetic_without_canonical_access(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "preview.html"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/preview.py"), "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.read_text().startswith("<!DOCTYPE html>"))

    def test_private_input_cannot_be_rendered_into_tracked_source(self):
        before = (ROOT / "template.html").read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "snapshot.json"
            source.write_text(json.dumps(snapshot()))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/preview.py"), "--snapshot", str(source), "--output", str(ROOT / "template.html")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("gitignored", result.stderr)
        self.assertEqual((ROOT / "template.html").read_bytes(), before)

    def test_cli_rejects_invalid_date_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "preview.html"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/preview.py"), "--as-of", "not-a-date", "--output", str(output)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_explicit_training_refresh_preserves_unknown_and_source_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            (repo / 'data').mkdir()
            sources = {'sessions': 'session_id,date,workout_type,status,duration_min\ns1,2026-08-20,strength,complete,\n',
                       'sets': 'session_id,status\n', 'cardio': 'session_id,duration_seconds\n'}
            for name, text in sources.items():
                (repo / 'data' / (name + '.csv')).write_text(text)
            data = snapshot()
            data['workouts'] = []
            prescriptions = data['training_detail']['prescriptions']
            refresh_training(data, repo)
            self.assertIsNone(data['training_detail']['days'][0]['total_minutes'])
            self.assertTrue(data['training'][0]['incomplete'])
            self.assertEqual(data['training_detail']['prescriptions'], prescriptions)
            for name, text in sources.items():
                self.assertEqual((repo / 'data' / (name + '.csv')).read_text(), text)
