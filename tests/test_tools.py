"""工具系统（沙箱 + 文件/命令/搜索工具 + 审批/diff 预览）的单元测试。"""
import tempfile
import unittest
from pathlib import Path

from tools import ToolRegistry, Workspace


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = Workspace(self.tmp)

    def test_resolve_relative(self):
        self.assertEqual(self.ws.resolve("a.py"), (self.tmp / "a.py").resolve())

    def test_traversal_blocked(self):
        with self.assertRaises(ValueError):
            self.ws.resolve("../etc/passwd")

    def test_truncate_spills_large_output(self):
        ws_small = Workspace(self.tmp, max_output_chars=50)
        r = ws_small.truncate("x" * 200)
        self.assertIn("已保存到", r)
        # 完整内容落盘可读回
        results = list((self.tmp / ".my_agent_core" / "results").rglob("*.txt"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].read_text(encoding="utf-8"), "x" * 200)
        self.assertEqual(self.ws.truncate("short"), "short")


class TestTools(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.reg = ToolRegistry(Workspace(self.tmp))

    def test_write_and_read(self):
        self.assertIn("已写入", self.reg.run("write_file", {"path": "a.txt", "content": "hello"}))
        self.assertIn("hello", self.reg.run("read_file", {"path": "a.txt"}))

    def test_read_nonexistent(self):
        self.assertIn("不存在", self.reg.run("read_file", {"path": "missing.txt"}))

    def test_edit_unique(self):
        self.reg.run("write_file", {"path": "a.txt", "content": "aaabbb"})
        r = self.reg.run("edit_file", {"path": "a.txt", "old_string": "bbb", "new_string": "ccc"})
        self.assertIn("已替换", r)
        self.assertIn("aaaccc", self.reg.run("read_file", {"path": "a.txt"}))

    def test_edit_ambiguous(self):
        self.reg.run("write_file", {"path": "a.txt", "content": "xx xx"})
        r = self.reg.run("edit_file", {"path": "a.txt", "old_string": "xx", "new_string": "yy"})
        self.assertIn("出现 2 次", r)

    def test_edit_missing(self):
        self.reg.run("write_file", {"path": "a.txt", "content": "abc"})
        r = self.reg.run("edit_file", {"path": "a.txt", "old_string": "zzz", "new_string": "y"})
        self.assertIn("未找到", r)

    def test_execute_command(self):
        r = self.reg.run("execute_command", {"command": "echo hi"})
        self.assertIn("hi", r)
        self.assertIn("退出码 0", r)

    def test_execute_failing_command(self):
        r = self.reg.run("execute_command", {"command": "exit 3"})
        self.assertIn("退出码 3", r)

    def test_search(self):
        self.reg.run("write_file", {"path": "a.txt", "content": "foo bar"})
        r = self.reg.run("search_files", {"query": "foo"})
        self.assertIn("a.txt:1", r)

    def test_list_and_delete(self):
        self.reg.run("write_file", {"path": "b.txt", "content": "x"})
        self.assertIn("b.txt", self.reg.run("list_files", {}))
        self.assertIn("已删除", self.reg.run("delete_file", {"path": "b.txt"}))
        self.assertNotIn("b.txt", self.reg.run("list_files", {}))

    def test_unknown_tool(self):
        self.assertIn("未知工具", self.reg.run("nope", {}))


class TestApproval(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_write_denied_returns_denial(self):
        decisions = [False]
        ws = Workspace(self.tmp, approve=lambda a, p, s: decisions.pop(0))
        reg = ToolRegistry(ws)
        r = reg.run("write_file", {"path": "a.txt", "content": "x"})
        self.assertIn("拒绝", r)
        self.assertFalse((self.tmp / "a.txt").exists())

    def test_write_approved_and_diff_shown(self):
        seen = []
        ws = Workspace(self.tmp, approve=lambda a, p, s: seen.append((a, p)) or True)
        reg = ToolRegistry(ws)
        reg.run("write_file", {"path": "a.txt", "content": "hello\nworld\n"})
        reg.run("edit_file", {"path": "a.txt", "old_string": "world", "new_string": "nju"})
        r = reg.run("write_file", {"path": "a.txt", "content": "hello\nnju\n"})
        self.assertIn("已写入", r)
        # 审批回调收到过 write_file 与 edit_file，且预览是统一 diff
        self.assertTrue(any("write_file" in a for a, _ in seen))
        self.assertTrue(any("edit_file" in a for a, _ in seen))
        self.assertTrue(any("-world" in p for _, p in seen))

    def test_dry_run_no_write(self):
        ws = Workspace(self.tmp)
        reg = ToolRegistry(ws)
        r = reg.run("write_file", {"path": "b.txt", "content": "data"}, dry_run=True)
        self.assertIn("[预览]", r)
        self.assertFalse((self.tmp / "b.txt").exists())
        # 预览后真正写入
        reg.run("write_file", {"path": "b.txt", "content": "data"})
        self.assertIn("data", reg.run("read_file", {"path": "b.txt"}))

    def test_confirm_denied_command(self):
        ws = Workspace(self.tmp, confirm=lambda c: False)
        reg = ToolRegistry(ws)
        r = reg.run("execute_command", {"command": "echo hi"})
        self.assertIn("拒绝", r)

    def test_delete_denied(self):
        (self.tmp / "c.txt").write_text("x", encoding="utf-8")
        ws = Workspace(self.tmp, approve=lambda a, p, s: False)
        reg = ToolRegistry(ws)
        r = reg.run("delete_file", {"path": "c.txt"})
        self.assertIn("拒绝", r)
        self.assertTrue((self.tmp / "c.txt").exists())


class TestChangeTracking(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_records_write_edit_delete(self):
        ws = Workspace(self.tmp)
        reg = ToolRegistry(ws)
        reg.run("write_file", {"path": "a.txt", "content": "hello"})
        reg.run("edit_file", {"path": "a.txt", "old_string": "hello", "new_string": "world"})
        reg.run("delete_file", {"path": "a.txt"})
        self.assertEqual([c.action for c in ws.changes], ["write", "edit", "delete"])

    def test_revert_delete_restores(self):
        ws = Workspace(self.tmp)
        reg = ToolRegistry(ws)
        reg.run("write_file", {"path": "a.txt", "content": "hello"})
        reg.run("delete_file", {"path": "a.txt"})
        ws.revert_change(ws.changes[1])  # 撤销删除
        self.assertEqual((self.tmp / "a.txt").read_text(encoding="utf-8"), "hello")

    def test_revert_write_new_removes(self):
        ws = Workspace(self.tmp)
        reg = ToolRegistry(ws)
        reg.run("write_file", {"path": "a.txt", "content": "hello"})
        ws.revert_change(ws.changes[0])  # 撤销新建
        self.assertFalse((self.tmp / "a.txt").exists())

    def test_dry_run_no_record(self):
        ws = Workspace(self.tmp)
        reg = ToolRegistry(ws)
        reg.run("write_file", {"path": "b.txt", "content": "x"}, dry_run=True)
        self.assertEqual(len(ws.changes), 0)
        self.assertFalse((self.tmp / "b.txt").exists())

    def test_denied_write_no_record(self):
        ws = Workspace(self.tmp, approve=lambda a, p, s: False)
        reg = ToolRegistry(ws)
        reg.run("write_file", {"path": "c.txt", "content": "x"})
        self.assertEqual(len(ws.changes), 0)


class TestParallelSafety(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry(Workspace(Path(tempfile.mkdtemp())))

    def test_read_only_parallel_safe(self):
        self.assertTrue(self.reg.is_parallel_safe("read_file"))
        self.assertTrue(self.reg.is_parallel_safe("list_files"))
        self.assertTrue(self.reg.is_parallel_safe("search_files"))

    def test_write_not_parallel_safe(self):
        self.assertFalse(self.reg.is_parallel_safe("write_file"))
        self.assertFalse(self.reg.is_parallel_safe("edit_file"))
        self.assertFalse(self.reg.is_parallel_safe("delete_file"))
        self.assertFalse(self.reg.is_parallel_safe("execute_command"))
        self.assertFalse(self.reg.is_parallel_safe("不存在的工具"))


if __name__ == "__main__":
    unittest.main()
