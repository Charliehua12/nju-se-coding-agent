"""会话管理器（多会话创建/切换/删除/持久化）的单元测试。"""
import json
import tempfile
import unittest
from pathlib import Path

from config import Config
from session import SessionManager
from tools import ToolRegistry, Workspace


class FakeClient:
    def chat(self, *args, **kwargs):
        raise NotImplementedError


def make_manager():
    cfg = Config(api_key="dummy")
    ws = Workspace(Path(tempfile.mkdtemp()))
    return SessionManager(cfg, FakeClient(), ToolRegistry(ws))


class TestSessionManager(unittest.TestCase):
    def test_new_and_auto_naming(self):
        m = make_manager()
        m.new()
        self.assertEqual(m.current, "会话1")
        m.new()
        self.assertEqual(m.current, "会话2")
        self.assertEqual(m.names(), ["会话1", "会话2"])

    def test_new_named_and_duplicate(self):
        m = make_manager()
        m.new("工作")
        self.assertEqual(m.current, "工作")
        with self.assertRaises(ValueError):
            m.new("工作")

    def test_switch(self):
        m = make_manager()
        m.new("a")
        m.new("b")
        self.assertIsNotNone(m.switch("a"))
        self.assertEqual(m.current, "a")
        self.assertIsNone(m.switch("不存在"))

    def test_remove_current_moves_to_next(self):
        m = make_manager()
        m.new("a")
        m.new("b")  # current = b
        self.assertTrue(m.remove("b"))
        self.assertEqual(m.current, "a")
        self.assertFalse(m.remove("不存在"))

    def test_save_and_load_roundtrip(self):
        m = make_manager()
        m.new("a")
        m.sessions["a"].load_history([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u"},
        ])
        m.new("b")
        path = Path(tempfile.mkdtemp()) / "sessions.json"
        m.save(str(path))

        m2 = make_manager()
        m2.load(str(path))
        self.assertEqual(sorted(m2.names()), ["a", "b"])
        self.assertEqual(m2.sessions["a"].dump_history()[0]["role"], "system")
        self.assertIsNotNone(m2.current_agent())

    def test_load_legacy_list_format(self):
        m = make_manager()
        path = Path(tempfile.mkdtemp()) / "legacy.json"
        path.write_text(json.dumps([{"role": "system", "content": "s"}]), encoding="utf-8")
        m.load(str(path))
        self.assertEqual(m.current, "会话1")
        self.assertEqual(m.current_agent().dump_history()[0]["role"], "system")

    def test_load_invalid_format(self):
        m = make_manager()
        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text(json.dumps("不是对象"), encoding="utf-8")
        with self.assertRaises(ValueError):
            m.load(str(path))


if __name__ == "__main__":
    unittest.main()
