"""Skills 技能系统（frontmatter 解析 / 目录发现 / 清单注入 / invoke_skill 工具）的单元测试。"""
import tempfile
import unittest
from pathlib import Path

import skills as skills_mod
from skills import (Skill, discover_skills, load_skill, parse_frontmatter,
                    skill_manifest, SKILLS_DIR)
from tools import ToolRegistry, Workspace


class TestFrontmatter(unittest.TestCase):
    def test_parse_basic(self):
        text = "---\nname: code-review\ndescription: 审查代码\n---\n正文..."
        self.assertEqual(parse_frontmatter(text),
                         {"name": "code-review", "description": "审查代码"})

    def test_no_frontmatter(self):
        self.assertEqual(parse_frontmatter("只有正文"), {})

    def test_parse_ignores_bad_lines(self):
        text = "---\nname: x\n没有冒号的行\ndescription: d\n---\n"
        self.assertEqual(parse_frontmatter(text), {"name": "x", "description": "d"})


class TestDiscover(unittest.TestCase):
    """项目技能的发现；把内置技能目录指向空处，隔离测试。"""

    def setUp(self):
        self._orig = skills_mod.AGENT_SKILLS_DIR
        skills_mod.AGENT_SKILLS_DIR = Path(tempfile.mkdtemp()) / "none"  # 无内置技能
        self.dir = Path(tempfile.mkdtemp())
        self.base = self.dir / SKILLS_DIR
        self.base.mkdir(parents=True)

    def tearDown(self):
        skills_mod.AGENT_SKILLS_DIR = self._orig

    def _skill(self, name, content):
        d = self.base / name
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text(content, encoding="utf-8")
        return d

    def test_discovers_sorted(self):
        self._skill("b-skill", "---\nname: b-skill\ndescription: B\n---\nbb\n")
        self._skill("a-skill", "---\nname: a-skill\ndescription: A\n---\naa\n")
        skills = discover_skills(self.dir)
        self.assertEqual([s.name for s in skills], ["a-skill", "b-skill"])

    def test_name_falls_back_to_dirname(self):
        self._skill("run-tests", "---\ndescription: 跑测试\n---\n指令\n")
        skills = discover_skills(self.dir)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "run-tests")

    def test_load_skill_lazy_body_strips_frontmatter(self):
        self._skill("s", "---\nname: s\ndescription: d\n---\n# 指令\n第 1 步\n")
        skill = discover_skills(self.dir)[0]
        self.assertEqual(skill.body, "")  # 未加载
        self.assertIn("# 指令", skill.load())  # 懒加载，正文剥离 frontmatter
        self.assertNotIn("name: s", skill.load())

    def test_empty_dir(self):
        self.assertEqual(discover_skills(self.dir), [])

    def test_missing_dir(self):
        self.assertEqual(discover_skills(Path(tempfile.mkdtemp())), [])


class TestBundledSkills(unittest.TestCase):
    """内置技能兜底：任何工作目录都能用上 agent 自带技能，项目可覆盖。"""

    def test_bundled_fallback_when_project_has_none(self):
        empty_dir = Path(tempfile.mkdtemp())
        names = [s.name for s in discover_skills(empty_dir)]
        self.assertIn("code-review", names)  # 内置 code-review 兜底可用

    def test_project_skill_merges_with_bundled(self):
        d = Path(tempfile.mkdtemp())
        base = d / SKILLS_DIR
        (base / "my-skill").mkdir(parents=True)
        (base / "my-skill" / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: 自定义\n---\n指令\n", encoding="utf-8")
        names = [s.name for s in discover_skills(d)]
        self.assertIn("my-skill", names)
        self.assertIn("code-review", names)  # 项目技能与内置并存

    def test_project_overrides_bundled_same_name(self):
        d = Path(tempfile.mkdtemp())
        base = d / SKILLS_DIR
        (base / "code-review").mkdir(parents=True)
        (base / "code-review" / "SKILL.md").write_text(
            "---\nname: code-review\ndescription: 项目版审查\n---\n项目指令\n",
            encoding="utf-8")
        skills = discover_skills(d)
        cr = next(s for s in skills if s.name == "code-review")
        # 同名以项目技能为准（正文是项目版而非内置版）
        self.assertIn("项目指令", cr.load())
        self.assertEqual(cr.path.resolve(), (base / "code-review" / "SKILL.md").resolve())


class TestManifest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(skill_manifest([]), "")

    def test_lists_name_and_description(self):
        s1 = Skill("a", "做 A", Path("x"))
        s2 = Skill("b", "做 B", Path("y"))
        m = skill_manifest([s1, s2])
        self.assertIn("a: 做 A", m)
        self.assertIn("b: 做 B", m)


class TestInvokeSkillTool(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = Workspace(self.tmp)
        self.reg = ToolRegistry(self.ws)

    def test_invoke_loads_body(self):
        base = self.tmp / SKILLS_DIR / "code-review"
        base.mkdir(parents=True)
        (base / "SKILL.md").write_text(
            "---\nname: code-review\ndescription: 审查\n---\n# 审查步骤\n1. 读文件\n",
            encoding="utf-8")
        self.ws.skills = discover_skills(self.tmp)
        r = self.reg.run("invoke_skill", {"name": "code-review"})
        self.assertIn("# 审查步骤", r)
        self.assertNotIn("name: code-review", r)  # 正文剥离 frontmatter

    def test_invoke_unknown(self):
        self.ws.skills = []
        r = self.reg.run("invoke_skill", {"name": "nope"})
        self.assertIn("未找到技能", r)

    def test_is_parallel_safe(self):
        self.assertTrue(self.reg.is_parallel_safe("invoke_skill"))


if __name__ == "__main__":
    unittest.main()
