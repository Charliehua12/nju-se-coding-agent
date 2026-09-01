"""Git 集成（自动 checkpoint / 只读监视）的单元测试。"""
import tempfile
import unittest
from pathlib import Path

from gitwrap import GitWrap


class TestGitWrap(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_init_creates_checkpoint_repo(self):
        g = GitWrap(self.tmp)
        self.assertEqual(g.init(), "checkpoint")
        self.assertTrue((self.tmp / ".git").exists())

    def test_watch_mode_when_existing_repo(self):
        GitWrap(self.tmp).init()  # 先建仓库
        g2 = GitWrap(self.tmp)
        self.assertEqual(g2.init(), "watch")

    def test_checkpoint_commits_changes(self):
        g = GitWrap(self.tmp)
        g.init()
        (self.tmp / "a.txt").write_text("hello", encoding="utf-8")
        self.assertTrue(g.is_dirty())
        self.assertTrue(g.checkpoint("创建 a.txt"))
        self.assertFalse(g.is_dirty())
        self.assertIn("a.txt", g.show_diff())

    def test_checkpoint_skips_when_clean(self):
        g = GitWrap(self.tmp)
        g.init()
        self.assertFalse(g.checkpoint("空提交"))

    def test_reset_all_rolls_back(self):
        g = GitWrap(self.tmp)
        g.init()
        (self.tmp / "a.txt").write_text("v1", encoding="utf-8")
        g.checkpoint("v1")
        (self.tmp / "a.txt").write_text("v2", encoding="utf-8")
        g.checkpoint("v2")
        out = g.reset_all()
        self.assertIn("回滚", out)
        self.assertEqual((self.tmp / "a.txt").read_text(encoding="utf-8"), "v1")

    def test_reset_all_single_checkpoint(self):
        g = GitWrap(self.tmp)
        g.init()
        (self.tmp / "a.txt").write_text("v1", encoding="utf-8")
        g.checkpoint("唯一提交")
        out = g.reset_all()
        self.assertIn("初始状态", out)
        self.assertFalse((self.tmp / "a.txt").exists())

    def test_watch_mode_never_auto_commits(self):
        GitWrap(self.tmp).init()  # 仓库已存在
        g = GitWrap(self.tmp)
        g.init()  # → watch
        (self.tmp / "b.txt").write_text("y", encoding="utf-8")
        self.assertTrue(g.is_dirty())
        self.assertFalse(g.checkpoint("不应提交"))  # watch 模式不提交


if __name__ == "__main__":
    unittest.main()
