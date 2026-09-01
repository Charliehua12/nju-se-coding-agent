"""长期记忆（MemoryStore）的单元测试。"""
import tempfile
import unittest
from pathlib import Path

from memory import MemoryStore


class TestMemoryStore(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_add_and_persist_across_sessions(self):
        store = MemoryStore(self.dir)
        store.add("项目使用 pytest")
        store2 = MemoryStore(self.dir)  # 重新加载 → 跨会话生效
        self.assertIn("pytest", store2.snapshot)

    def test_dedupe(self):
        store = MemoryStore(self.dir)
        store.add("约定X")
        r = store.add("约定X")
        self.assertIn("已存在", r)

    def test_replace_and_remove(self):
        store = MemoryStore(self.dir)
        store.add("用 pytest")
        store.replace("pytest", "unittest")
        self.assertIn("unittest", MemoryStore(self.dir).snapshot)
        store.remove("unittest")
        self.assertNotIn("unittest", MemoryStore(self.dir).snapshot)

    def test_over_limit_rejected(self):
        store = MemoryStore(self.dir)
        r = store.add("长" * 3000)
        self.assertIn("超出上限", r)

    def test_snapshot_frozen_during_session(self):
        store = MemoryStore(self.dir)
        store.add("旧记忆")
        snap = store.snapshot
        store.add("新记忆")
        # 会话内快照静止（冻结），新记忆下次会话才生效
        self.assertEqual(store.snapshot, snap)


if __name__ == "__main__":
    unittest.main()
