"""
test_config.py — Production tests for config loading, caching, and auto-init.
==============================================================================
Covers:
  - Normal config load/save cycle
  - Config caching (TTL-based invalidation)
  - Auto-init from config.example.json when config.json is missing
  - Corrupted JSON file handling
  - Cache invalidation after save_config()
  - Missing example config fallback
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch


class TestConfigLoadSave(unittest.TestCase):
    """Basic config load/save with real file I/O in temp directory."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._data_dir = os.path.join(self._tmpdir, "data")
        os.makedirs(self._data_dir, exist_ok=True)
        self._config_path = os.path.join(self._data_dir, "config.json")
        self._example_path = os.path.join(self._tmpdir, "config.example.json")

        # Reset module-level cache before each test
        import app.core.config as cfg_mod
        cfg_mod._config_cache = {}
        cfg_mod._config_cache_time = 0.0

    def tearDown(self):
        shutil.rmtree(self._tmpdir)

    def _write_config(self, data: dict):
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    @patch("app.core.config.CONFIG_PATH")
    @patch("app.core.config._EXAMPLE_CONFIG_PATH")
    def test_load_returns_config_data(self, mock_example, mock_path):
        mock_path.__str__ = lambda s: self._config_path
        # Patch as simple string (CONFIG_PATH is str, not Path)
        import app.core.config as cfg_mod
        original_path = cfg_mod.CONFIG_PATH
        original_example = cfg_mod._EXAMPLE_CONFIG_PATH
        cfg_mod.CONFIG_PATH = self._config_path
        cfg_mod._EXAMPLE_CONFIG_PATH = self._example_path
        try:
            self._write_config({"model_name": "gemini-2.0-flash", "max_hr": 190})

            result = cfg_mod.load_config()
            self.assertEqual(result["model_name"], "gemini-2.0-flash")
            self.assertEqual(result["max_hr"], 190)
        finally:
            cfg_mod.CONFIG_PATH = original_path
            cfg_mod._EXAMPLE_CONFIG_PATH = original_example

    @patch("app.core.config.CONFIG_PATH")
    @patch("app.core.config._EXAMPLE_CONFIG_PATH")
    def test_save_then_load_roundtrip(self, mock_example, mock_path):
        import app.core.config as cfg_mod
        original_path = cfg_mod.CONFIG_PATH
        original_example = cfg_mod._EXAMPLE_CONFIG_PATH
        cfg_mod.CONFIG_PATH = self._config_path
        cfg_mod._EXAMPLE_CONFIG_PATH = self._example_path
        try:
            cfg_mod.save_config({"rest_hr": 50, "race_date": "2026-06-01"})

            # Cache should be invalidated after save
            self.assertEqual(cfg_mod._config_cache, {})

            result = cfg_mod.load_config()
            self.assertEqual(result["rest_hr"], 50)
            self.assertEqual(result["race_date"], "2026-06-01")
        finally:
            cfg_mod.CONFIG_PATH = original_path
            cfg_mod._EXAMPLE_CONFIG_PATH = original_example


class TestConfigCaching(unittest.TestCase):
    """Config should be cached for 60s to avoid repeated disk reads."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._data_dir = os.path.join(self._tmpdir, "data")
        os.makedirs(self._data_dir, exist_ok=True)
        self._config_path = os.path.join(self._data_dir, "config.json")
        self._example_path = os.path.join(self._tmpdir, "config.example.json")

        import app.core.config as cfg_mod
        cfg_mod._config_cache = {}
        cfg_mod._config_cache_time = 0.0

    def tearDown(self):
        shutil.rmtree(self._tmpdir)

    def test_second_load_uses_cache(self):
        import app.core.config as cfg_mod
        original_path = cfg_mod.CONFIG_PATH
        original_example = cfg_mod._EXAMPLE_CONFIG_PATH
        cfg_mod.CONFIG_PATH = self._config_path
        cfg_mod._EXAMPLE_CONFIG_PATH = self._example_path
        try:
            with open(self._config_path, "w") as f:
                json.dump({"version": 1}, f)

            result1 = cfg_mod.load_config()
            self.assertEqual(result1["version"], 1)

            # Modify file on disk — but cache should still return old value
            with open(self._config_path, "w") as f:
                json.dump({"version": 2}, f)

            result2 = cfg_mod.load_config()
            self.assertEqual(result2["version"], 1, "Second call should use cache")
        finally:
            cfg_mod.CONFIG_PATH = original_path
            cfg_mod._EXAMPLE_CONFIG_PATH = original_example

    def test_save_invalidates_cache(self):
        import app.core.config as cfg_mod
        original_path = cfg_mod.CONFIG_PATH
        original_example = cfg_mod._EXAMPLE_CONFIG_PATH
        cfg_mod.CONFIG_PATH = self._config_path
        cfg_mod._EXAMPLE_CONFIG_PATH = self._example_path
        try:
            with open(self._config_path, "w") as f:
                json.dump({"version": 1}, f)

            cfg_mod.load_config()  # Populate cache
            cfg_mod.save_config({"version": 3})

            result = cfg_mod.load_config()
            self.assertEqual(result["version"], 3, "Cache should be invalidated after save")
        finally:
            cfg_mod.CONFIG_PATH = original_path
            cfg_mod._EXAMPLE_CONFIG_PATH = original_example


class TestConfigAutoInit(unittest.TestCase):
    """When config.json is missing, auto-copy from config.example.json."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._data_dir = os.path.join(self._tmpdir, "data")
        os.makedirs(self._data_dir, exist_ok=True)
        self._config_path = os.path.join(self._data_dir, "config.json")
        self._example_path = os.path.join(self._tmpdir, "config.example.json")

        import app.core.config as cfg_mod
        cfg_mod._config_cache = {}
        cfg_mod._config_cache_time = 0.0

    def tearDown(self):
        shutil.rmtree(self._tmpdir)

    def test_auto_init_from_example(self):
        import app.core.config as cfg_mod
        original_path = cfg_mod.CONFIG_PATH
        original_example = cfg_mod._EXAMPLE_CONFIG_PATH
        cfg_mod.CONFIG_PATH = self._config_path
        cfg_mod._EXAMPLE_CONFIG_PATH = self._example_path
        try:
            # Create example config but NOT config.json
            with open(self._example_path, "w") as f:
                json.dump({"model_name": "default-model", "max_hr": 185}, f)

            # config.json should NOT exist yet
            self.assertFalse(os.path.exists(self._config_path))

            result = cfg_mod.load_config()

            # config.json should now exist (auto-copied)
            self.assertTrue(os.path.exists(self._config_path))
            self.assertEqual(result["model_name"], "default-model")
        finally:
            cfg_mod.CONFIG_PATH = original_path
            cfg_mod._EXAMPLE_CONFIG_PATH = original_example

    def test_no_example_returns_empty(self):
        """If both config.json AND config.example.json are missing."""
        import app.core.config as cfg_mod
        original_path = cfg_mod.CONFIG_PATH
        original_example = cfg_mod._EXAMPLE_CONFIG_PATH
        cfg_mod.CONFIG_PATH = self._config_path
        cfg_mod._EXAMPLE_CONFIG_PATH = self._example_path
        try:
            # Neither file exists
            self.assertFalse(os.path.exists(self._config_path))
            self.assertFalse(os.path.exists(self._example_path))

            result = cfg_mod.load_config()
            self.assertEqual(result, {})
        finally:
            cfg_mod.CONFIG_PATH = original_path
            cfg_mod._EXAMPLE_CONFIG_PATH = original_example


class TestCorruptedConfig(unittest.TestCase):
    """Corrupted config.json should not crash the system."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._data_dir = os.path.join(self._tmpdir, "data")
        os.makedirs(self._data_dir, exist_ok=True)
        self._config_path = os.path.join(self._data_dir, "config.json")
        self._example_path = os.path.join(self._tmpdir, "config.example.json")

        import app.core.config as cfg_mod
        cfg_mod._config_cache = {}
        cfg_mod._config_cache_time = 0.0

    def tearDown(self):
        shutil.rmtree(self._tmpdir)

    def test_invalid_json_returns_empty_dict(self):
        import app.core.config as cfg_mod
        original_path = cfg_mod.CONFIG_PATH
        original_example = cfg_mod._EXAMPLE_CONFIG_PATH
        cfg_mod.CONFIG_PATH = self._config_path
        cfg_mod._EXAMPLE_CONFIG_PATH = self._example_path
        try:
            with open(self._config_path, "w") as f:
                f.write("{invalid json content!!!")

            result = cfg_mod.load_config()
            self.assertEqual(result, {})
        finally:
            cfg_mod.CONFIG_PATH = original_path
            cfg_mod._EXAMPLE_CONFIG_PATH = original_example

    def test_empty_file_returns_empty_dict(self):
        import app.core.config as cfg_mod
        original_path = cfg_mod.CONFIG_PATH
        original_example = cfg_mod._EXAMPLE_CONFIG_PATH
        cfg_mod.CONFIG_PATH = self._config_path
        cfg_mod._EXAMPLE_CONFIG_PATH = self._example_path
        try:
            with open(self._config_path, "w") as f:
                f.write("")

            result = cfg_mod.load_config()
            self.assertEqual(result, {})
        finally:
            cfg_mod.CONFIG_PATH = original_path
            cfg_mod._EXAMPLE_CONFIG_PATH = original_example


class TestConfigThreadSafety(unittest.TestCase):
    """P3.7: threading.Lock added to config cache — verify no data races under concurrent access."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._config_path = os.path.join(self._tmpdir, "data", "config.json")
        os.makedirs(os.path.dirname(self._config_path))

        import app.core.config as cfg_mod
        cfg_mod._config_cache = {}
        cfg_mod._config_cache_time = 0.0
        self._original_path = cfg_mod.CONFIG_PATH
        self._original_example = cfg_mod._EXAMPLE_CONFIG_PATH
        cfg_mod.CONFIG_PATH = self._config_path
        cfg_mod._EXAMPLE_CONFIG_PATH = os.path.join(self._tmpdir, "config.example.json")
        with open(self._config_path, "w") as f:
            json.dump({"thread_test": True, "counter": 0}, f)

    def tearDown(self):
        import app.core.config as cfg_mod
        cfg_mod.CONFIG_PATH = self._original_path
        cfg_mod._EXAMPLE_CONFIG_PATH = self._original_example
        cfg_mod._config_cache = {}
        cfg_mod._config_cache_time = 0.0
        shutil.rmtree(self._tmpdir)

    def test_concurrent_load_config_returns_consistent_data(self):
        """Many threads reading load_config() simultaneously must all get the same result."""
        import threading
        import app.core.config as cfg_mod

        results = []
        errors = []

        def reader():
            try:
                results.append(cfg_mod.load_config())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 20)
        for r in results:
            self.assertTrue(r.get("thread_test"))

    def test_concurrent_save_and_load_no_corruption(self):
        """Interleaved save_config + load_config from multiple threads must not corrupt data."""
        import threading
        import app.core.config as cfg_mod

        errors = []

        def writer(n: int):
            try:
                cfg_mod.save_config({"value": n})
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                data = cfg_mod.load_config()
                # Just verify it's a dict and not corrupted (partial write)
                assert isinstance(data, dict)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
