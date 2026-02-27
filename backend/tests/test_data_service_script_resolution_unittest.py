import sys
import unittest
from pathlib import Path
import shutil
import uuid

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.data_service import DataService


class TestDataServiceScriptResolution(unittest.TestCase):
    def _make_temp_root(self) -> Path:
        root = Path("backend") / "tests" / "_tmp" / f"ds_path_{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_resolve_prefers_existing_backend_scripts(self):
        root = self._make_temp_root()
        (root / "backend" / "scripts").mkdir(parents=True, exist_ok=True)
        target = root / "backend" / "scripts" / "fetch_market_environment.py"
        target.write_text("# test\n", encoding="utf-8")

        svc = DataService()
        path, searched = svc._resolve_script_path(root, "fetch_market_environment.py")
        self.assertIsNotNone(path)
        self.assertEqual(path, target)
        self.assertGreaterEqual(len(searched), 3)

    def test_resolve_missing_returns_candidates(self):
        root = self._make_temp_root()
        svc = DataService()
        path, searched = svc._resolve_script_path(root, "missing_script.py")
        self.assertIsNone(path)
        self.assertEqual(len(searched), 3)
        self.assertTrue(any("backend" in p for p in searched))


if __name__ == "__main__":
    unittest.main()
