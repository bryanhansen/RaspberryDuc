import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from raspberryduc.config import (
    AppConfig,
    DurableFileHandler,
    apply_logging,
    load_config,
    parse_log_stamp,
    prune_session_logs,
    session_log_name,
)


class ConfigTests(unittest.TestCase):
    def test_missing_file_disables_logging(self) -> None:
        cfg = load_config(Path(tempfile.gettempdir()) / "no-such-raspberryduc.ini")
        self.assertFalse(cfg.logging_enabled)

    def test_enabled_true_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raspberryduc.ini"
            path.write_text(
                "[logging]\nenabled = true\ndirectory = traces\n",
                encoding="utf-8",
            )
            cfg = load_config(path)
            self.assertTrue(cfg.logging_enabled)
            self.assertEqual(cfg.log_dir, (Path(tmp) / "traces").resolve())
            self.assertEqual(cfg.config_path, path.resolve())

    def test_enabled_false_by_default_in_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raspberryduc.ini"
            path.write_text("[logging]\nenabled = false\n", encoding="utf-8")
            cfg = load_config(path)
            self.assertFalse(cfg.logging_enabled)

    def test_session_log_name_yyyy_dd_mm(self) -> None:
        when = datetime(2026, 9, 6, 14, 3, 22)
        self.assertEqual(session_log_name(when), "raspberryduc_2026_06_09_14_03_22.log")
        self.assertEqual(parse_log_stamp("raspberryduc_2026_06_09_14_03_22.log"), when)

    def test_prune_keeps_five_newest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            stamps = [
                datetime(2026, 1, 1, 0, 0, n)
                for n in range(1, 7)
            ]
            paths = []
            for stamp in stamps:
                path = directory / session_log_name(stamp)
                path.write_text("x", encoding="utf-8")
                paths.append(path)
            prune_session_logs(directory, keep=5)
            remaining = {p.name for p in directory.iterdir()}
            self.assertNotIn(paths[0].name, remaining)
            self.assertEqual(len(remaining), 5)

    def test_apply_logging_creates_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "logs"
            cfg = AppConfig(logging_enabled=True, log_dir=directory)
            when = datetime(2026, 9, 6, 8, 15, 0)
            created = apply_logging(cfg, now=when)
            self.assertIsNotNone(created)
            assert created is not None
            self.assertTrue(created.is_file())
            self.assertEqual(created.name, "raspberryduc_2026_06_09_08_15_00.log")
            self.assertIn(
                "ECU transaction logging enabled",
                created.read_text(encoding="utf-8"),
            )
            package = logging.getLogger("raspberryduc")
            for handler in list(package.handlers):
                if getattr(handler, "baseFilename", None) == str(created):
                    handler.close()
                    package.removeHandler(handler)

    def test_durable_handler_visible_without_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.log"
            handler = DurableFileHandler(path, mode="w", encoding="utf-8")
            log = logging.getLogger("raspberryduc.durable_test")
            log.handlers.clear()
            log.setLevel(logging.DEBUG)
            log.propagate = False
            log.addHandler(handler)
            log.info("fsync-marker")
            self.assertIn("fsync-marker", path.read_text(encoding="utf-8"))
            log.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    unittest.main()
