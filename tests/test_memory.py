"""长期记忆（MemoryStore）的单元测试。"""
import tempfile
import unittest
from pathlib import Path

from memory import MemoryStore, MEMORY_FILE, USER_CHAR_LIMIT, USER_FILE


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


class TestUserProfile(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_separate_files(self):
        # 项目记忆与用户画像写在不同文件，互不干扰
        mem = MemoryStore(self.dir)
        usr = MemoryStore(self.dir, file=USER_FILE, char_limit=USER_CHAR_LIMIT)
        mem.add("项目使用 pytest")
        usr.add("用户偏好 Python 类型注解")
        # 重新加载（跨会话生效）：两类记忆各归各的文件
        mem2 = MemoryStore(self.dir)
        usr2 = MemoryStore(self.dir, file=USER_FILE, char_limit=USER_CHAR_LIMIT)
        self.assertIn("pytest", mem2.snapshot)
        self.assertNotIn("pytest", usr2.snapshot)
        self.assertIn("类型注解", usr2.snapshot)
        self.assertNotIn("类型注解", mem2.snapshot)
        self.assertTrue((self.dir / MEMORY_FILE).exists())
        self.assertTrue((self.dir / USER_FILE).exists())

    def test_user_has_smaller_limit(self):
        usr = MemoryStore(self.dir, file=USER_FILE, char_limit=USER_CHAR_LIMIT)
        r = usr.add("长" * (USER_CHAR_LIMIT + 10))
        self.assertIn("超出上限", r)
        # 上限信息随实例
        self.assertIn(str(USER_CHAR_LIMIT), r)

    def test_default_still_memory_file(self):
        store = MemoryStore(self.dir)
        store.add("默认进 MEMORY.md")
        self.assertTrue((self.dir / MEMORY_FILE).exists())


if __name__ == "__main__":
    unittest.main()
