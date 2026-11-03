"""Exercise report output, rich text, provenance and protection of edited drafts."""

from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "render_report.py"
SPEC = importlib.util.spec_from_file_location("weekly_report_renderer", SCRIPT)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def sample():
    # Synthetic facts, deliberately including failed and blocked cases.
    return {
        "period_start": "2026-11-02",
        "period_end": "2026-11-08",
        "generated_at": "2026-11-08T16:30:00Z",
        "feishu_url": "https://oa.feishu.cn/report/record/detail?ruleId=123&from=a%3Ab&x=1&x=2",
        "summary": "完成2册手册编制，并开展专项测试。",
        "sources": {"s1": "本周记录：演示素材", "s2": "用户确认：无补充事项"},
        "sections": {
            "progress": [
                {"title": "文档编制", "body": "完成2册独立手册，分别输出Word及PDF。", "source_ids": ["s1"]},
                {"title": "专项测试", "body": "执行20项用例，通过15项、失败3项、阻塞2项；尚未部署。", "source_ids": ["s1"]},
            ],
            "plans": [
                {"title": "补齐操作配图", "body": "根据遗留事项补充配图，具体范围待确认。", "source_ids": ["s1"], "suggested": True},
            ],
            "coordination": "待确认",
            "other": "无",
        },
        "pending": ["确认是否需要协调演示记录"],
    }


class ParagraphReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs = []
        self.parts = None
        self.strong = False
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == "p":
            self.parts = []
        if tag == "strong":
            self.strong = True

    def handle_endtag(self, tag):
        if tag == "strong":
            self.strong = False
        if tag == "p":
            self.paragraphs.append(self.parts)
            self.parts = None

    def handle_data(self, data):
        if self.parts is not None:
            self.parts.append((data, self.strong))


class ReportRenderingTests(unittest.TestCase):
    def test_four_fields_have_fixed_order_and_labels(self):
        _, payload = renderer.build_report(sample())
        self.assertEqual(list(payload["fields"]), ["progress", "plans", "coordination", "other"])
        self.assertEqual([field["label"] for field in payload["fields"].values()], [
            "本周工作/项目推进重点进展&总结", "下周工作计划与安排", "需要协调与帮助", "其他需要同步的事项或说明",
        ])

    def test_titles_and_bodies_share_paragraph_and_only_titles_are_bold(self):
        _, payload = renderer.build_report(sample())
        parser = ParagraphReader()
        parser.feed(payload["fields"]["progress"]["html"])
        self.assertEqual(parser.paragraphs[0], [(sample()["summary"], False)])
        self.assertEqual(parser.paragraphs[1], [])
        self.assertEqual(parser.paragraphs[2], [
            ("1. 文档编制：", True), ("完成2册独立手册，分别输出Word及PDF。", False),
        ])
        self.assertEqual(parser.paragraphs[3], [])
        self.assertEqual(parser.paragraphs[4][0], ("2. 专项测试：", True))

    def test_markdown_and_plaintext_keep_blank_lines_between_items(self):
        _, payload = renderer.build_report(sample())
        field = payload["fields"]["progress"]
        self.assertEqual(len(field["text"].split("\n\n")), 3)
        self.assertTrue(all("\n" not in item for item in field["text"].split("\n\n")))
        self.assertIn("**1. 文档编制：**完成2册", field["markdown"])
        self.assertNotIn("**", field["text"])

    def test_unsafe_html_and_markup_remain_literal_text(self):
        data = sample()
        title = '图表 <script>alert("x")</script> & *记录*'
        body = "保留 <img src=x onerror=alert(1)> 与 **原文**、[来源](file)。"
        data["sections"]["progress"][0].update(title=title, body=body)
        _, payload = renderer.build_report(data)
        field = payload["fields"]["progress"]
        parser = ParagraphReader()
        parser.feed(field["html"])
        self.assertNotIn("script", parser.tags)
        self.assertNotIn("img", parser.tags)
        self.assertEqual(parser.paragraphs[2], [(f"1. {title}：", True), (body, False)])
        self.assertIn(r"\*\*原文\*\*", field["markdown"])
        self.assertIn(r"\[来源\]", field["markdown"])

    def test_selection_offsets_work_with_emoji_and_two_titles(self):
        data = sample()
        data["summary"] = "本周完成📘文档。"
        data["sections"]["progress"][0]["title"] = "文档📘编制"
        _, payload = renderer.build_report(data)
        field = payload["fields"]["progress"]
        encoded = field["text"].encode("utf-16-le")
        headings = [encoded[span["start"] * 2:span["end"] * 2].decode("utf-16-le") for span in field["title_spans"]]
        self.assertEqual(headings, ["1. 文档📘编制：", "2. 专项测试："])

    def test_facts_and_stage_limits_are_preserved_without_invented_outcomes(self):
        _, payload = renderer.build_report(sample())
        text = payload["fields"]["progress"]["text"]
        for item in sample()["sections"]["progress"]:
            self.assertIn(item["body"], text)
        self.assertNotIn("100%", text)
        self.assertNotIn("已部署", text)

    def test_suggested_plan_is_marked_without_altering_confirmed_plan(self):
        data = sample()
        data["sections"]["plans"].append({
            "title": "参加会议", "body": "按用户安排参会。", "source_ids": ["s1"],
        })
        _, payload = renderer.build_report(data)
        text = payload["fields"]["plans"]["text"]
        self.assertIn("1. 补齐操作配图（建议，待确认）：", text)
        self.assertIn("2. 参加会议：按用户安排参会。", text)
        self.assertEqual(text.count("建议，待确认"), 1)

    def test_pending_sources_and_metadata_never_leak_into_form_fields(self):
        markdown, payload = renderer.build_report(sample())
        serialized = json.dumps(payload["fields"], ensure_ascii=False)
        for private_text in ["本周记录：演示素材", "确认是否需要协调演示记录", "飞书处理状态", sample()["feishu_url"]]:
            self.assertIn(private_text, markdown)
            self.assertNotIn(private_text, serialized)

    def test_beijing_conversion_crosses_midnight_and_preserves_actual_period(self):
        data = sample()
        data["period_start"] = "2026-11-01"
        data["period_end"] = "2026-11-06"
        markdown, payload = renderer.build_report(data)
        self.assertIn("2026-11-09 00:30（北京时间）", markdown)
        self.assertEqual(payload["generated_at"], "2026-11-09T00:30:00+08:00")
        self.assertEqual(payload["period_start"], "2026-11-01")
        self.assertEqual(payload["period_end"], "2026-11-06")

    def test_default_timestamp_is_explicitly_beijing(self):
        data = sample()
        del data["generated_at"]
        _, payload = renderer.build_report(data)
        self.assertTrue(payload["generated_at"].endswith("+08:00"))

    def test_link_preserves_encoded_and_repeated_query_parameters(self):
        data = sample()
        markdown, payload = renderer.build_report(data)
        self.assertEqual(payload["feishu_url"], data["feishu_url"])
        self.assertIn("飞书周报链接：" + data["feishu_url"], markdown)

    def test_absent_link_and_confirmed_empty_sections(self):
        data = sample()
        del data["feishu_url"]
        data["sections"]["coordination"] = "无"
        markdown, payload = renderer.build_report(data)
        self.assertIn("飞书周报链接：待用户提供", markdown)
        for key in ["coordination", "other"]:
            self.assertEqual(payload["fields"][key]["text"], "无")
            self.assertEqual(payload["fields"][key]["html"], "<p>无</p>")

    def test_coordination_items_are_numbered_and_bold_in_one_paragraph(self):
        data = sample()
        data["sections"]["coordination"] = [{
            "title": "协调演示记录", "body": "请业务人员提供配图所需的演示记录。", "source_ids": ["s1"],
        }]
        _, payload = renderer.build_report(data)
        self.assertEqual(payload["fields"]["coordination"]["html"],
                         "<p><strong>1. 协调演示记录：</strong>请业务人员提供配图所需的演示记录。</p>")

    def test_invalid_inputs_fail_instead_of_silently_dropping_material(self):
        invalid = []
        for key, value in [("period_end", "2026-10-01"), ("period_start", "2026-02-30"),
                           ("period_start", "20261102"), ("generated_at", "2026-11-08T19:00:00"),
                           ("generated_at", "invalid"), ("feishu_url", "javascript:alert(1)"),
                           ("feishu_url", "https://user:pass@oa.feishu.cn/report"),
                           ("feishu_url", " https://oa.feishu.cn/report "),
                           ("sources", {}), ("pending", "不是列表"), ("submit", True)]:
            data = sample()
            data[key] = value
            invalid.append((key, data))
        for name, mutate in [
            ("missing field", lambda d: d["sections"].pop("other")),
            ("extra field", lambda d: d["sections"].update(extra="无")),
            ("empty progress", lambda d: d["sections"].update(progress=[])),
            ("text progress", lambda d: d["sections"].update(progress="无")),
            ("missing source", lambda d: d["sections"]["progress"][0].update(source_ids=["unknown"])),
            ("empty source", lambda d: d["sections"]["progress"][0].update(source_ids=[])),
            ("multiline title", lambda d: d["sections"]["progress"][0].update(title="标题\n正文")),
            ("multiline body", lambda d: d["sections"]["progress"][0].update(body="正文\r\n下一段")),
            ("unicode newline", lambda d: d["sections"]["progress"][0].update(body="正文\u2028下一段")),
            ("empty body", lambda d: d["sections"]["progress"][0].update(body="")),
            ("string boolean", lambda d: d["sections"]["plans"][0].update(suggested="false")),
            ("suggested result", lambda d: d["sections"]["progress"][0].update(suggested=True)),
        ]:
            data = sample()
            mutate(data)
            invalid.append((name, data))
        for name, data in invalid:
            with self.subTest(name=name), self.assertRaises(renderer.DraftError):
                renderer.build_report(data)

    def test_documented_json_example_is_executable(self):
        reference = (SKILL / "references" / "draft-format.md").read_text(encoding="utf-8")
        match = re.search(r"```json\n(.*?)\n```", reference, re.DOTALL)
        markdown, payload = renderer.build_report(json.loads(match.group(1)))
        self.assertIn("2册手册", markdown)
        self.assertEqual(len(payload["fields"]), 4)

    def test_documentation_links_resolve_inside_standalone_repository(self):
        documents = [SKILL / "README.md", SKILL / "README.zh-CN.md", SKILL / "SKILL.md"]
        for document in documents:
            content = document.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", content):
                if "://" in target or target.startswith("#"):
                    continue
                resolved = (document.parent / target.split("#", 1)[0]).resolve()
                with self.subTest(document=document.name, target=target):
                    self.assertIn(SKILL.resolve(), resolved.parents)
                    self.assertTrue(resolved.is_file(), f"Broken repository link: {target}")


class FileAndCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / "周报" / "本周工作周报.md"
        self.markdown, self.payload = renderer.build_report(sample())

    def test_local_file_roundtrip_and_initial_status(self):
        paths, backup = renderer.write_outputs(self.output, self.markdown, self.payload)
        self.assertIsNone(backup)
        self.assertEqual(paths[0].read_text(encoding="utf-8"), self.markdown)
        self.assertEqual(json.loads(paths[1].read_text(encoding="utf-8")), self.payload)
        self.assertIn("状态：本地草稿，未提交", self.markdown)
        self.assertIn("飞书处理状态：未填写，未核验", self.markdown)

    def test_existing_user_edits_are_protected_by_default(self):
        paths, _ = renderer.write_outputs(self.output, self.markdown, self.payload)
        paths[0].write_text("用户已修改的正文", encoding="utf-8")
        companion_before = paths[1].read_bytes()
        with self.assertRaises(renderer.DraftError):
            renderer.write_outputs(self.output, "新草稿", self.payload)
        self.assertEqual(paths[0].read_text(encoding="utf-8"), "用户已修改的正文")
        self.assertEqual(paths[1].read_bytes(), companion_before)

    def test_replace_backs_up_both_files_before_overwriting(self):
        paths, _ = renderer.write_outputs(self.output, self.markdown, self.payload)
        paths[0].write_text("用户最终修改", encoding="utf-8")
        old_companion = paths[1].read_bytes()
        _, backup = renderer.write_outputs(self.output, "已合并的新稿", self.payload, replace=True)
        self.assertEqual((backup / paths[0].name).read_text(encoding="utf-8"), "用户最终修改")
        self.assertEqual((backup / paths[1].name).read_bytes(), old_companion)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "已合并的新稿")

    def test_existing_companion_alone_prevents_partial_overwrite(self):
        self.output.parent.mkdir()
        companion = self.output.with_suffix(".fields.json")
        companion.write_text("旧格式载荷", encoding="utf-8")
        with self.assertRaises(renderer.DraftError):
            renderer.write_outputs(self.output, self.markdown, self.payload)
        self.assertFalse(self.output.exists())
        self.assertEqual(companion.read_text(encoding="utf-8"), "旧格式载荷")

    def test_symlink_target_cannot_overwrite_another_file(self):
        original = self.root / "不可覆盖.md"
        original.write_text("保留", encoding="utf-8")
        self.output.parent.mkdir()
        self.output.symlink_to(original)
        with self.assertRaises(renderer.DraftError):
            renderer.write_outputs(self.output, self.markdown, self.payload, replace=True)
        self.assertEqual(original.read_text(encoding="utf-8"), "保留")

    def run_cli(self, source, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(source), "--output", str(self.output), *extra],
            capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_cli_generates_readable_local_outputs(self):
        source = self.root / "事实.json"
        source.write_text(json.dumps(sample(), ensure_ascii=False), encoding="utf-8")
        result = self.run_cli(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["markdown"], str(self.output))
        self.assertIn("未填写飞书，未提交", output["status"])
        self.assertEqual(json.loads(Path(output["fields"]).read_text(encoding="utf-8")), self.payload)

    def test_cli_invalid_input_does_not_create_output(self):
        source = self.root / "错误事实.json"
        data = sample()
        data["sections"]["progress"][0]["source_ids"] = ["不存在的来源"]
        source.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_cli(source)
        self.assertEqual(result.returncode, 2)
        self.assertIn("必须引用已提供的来源", result.stderr)
        self.assertFalse(self.output.parent.exists())

    def test_cli_rejects_input_output_collision_even_with_replace(self):
        self.output.parent.mkdir()
        content = json.dumps(sample(), ensure_ascii=False)
        self.output.write_text(content, encoding="utf-8")
        result = self.run_cli(self.output, "--replace")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.output.read_text(encoding="utf-8"), content)


if __name__ == "__main__":
    unittest.main()
