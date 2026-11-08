#!/usr/bin/env python3
"""Render a local weekly draft and rich-text fields; never connect to Feishu."""

import argparse
from datetime import date, datetime
from html import escape
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
SECTION_LABELS = {
    "progress": "本周工作/项目推进重点进展&总结",
    "plans": "下周工作计划与安排",
    "coordination": "需要协调与帮助",
    "other": "其他需要同步的事项或说明",
}


class DraftError(ValueError):
    """Input or output cannot safely produce the requested local draft."""


def single_line(value, location):
    if not isinstance(value, str) or not value.strip():
        raise DraftError(f"{location} 必须是非空字符串")
    if any(char in value for char in "\n\r\u2028\u2029"):
        raise DraftError(f"{location} 不得包含字段内换行；标题和正文的分行由工具生成")
    if any(ord(char) < 32 for char in value):
        raise DraftError(f"{location} 不得包含控制字符")
    return value.strip()


def markdown_text(value):
    return re.sub(r"([\\`*_\[\]<>])", r"\\\1", value)


def utf16_length(value):
    return len(value.encode("utf-16-le")) // 2


def parse_date(value, location):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise DraftError(f"{location} 必须为 YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise DraftError(f"{location} 不是有效日期") from error


def generated_time(value):
    if value is None:
        return datetime.now(BEIJING)
    if not isinstance(value, str):
        raise DraftError("generated_at 必须是带时区的 ISO 日期时间")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DraftError("generated_at 必须是带时区的 ISO 日期时间") from error
    if result.tzinfo is None:
        raise DraftError("generated_at 缺少时区")
    return result.astimezone(BEIJING)


def render_field(key, value, sources, summary):
    paragraphs = []
    source_rows = []
    if key == "progress" and summary:
        paragraphs.append((summary, markdown_text(summary), escape(summary), None))
    if isinstance(value, str) and key != "progress":
        value = single_line(value, key)
        paragraphs.append((value, markdown_text(value), escape(value), None))
    else:
        if not isinstance(value, list) or not value:
            raise DraftError(f"{key} 必须有工作项；无事项请明确写无或待确认")
        for number, item in enumerate(value, 1):
            location = f"{key}[{number}]"
            if not isinstance(item, dict):
                raise DraftError(f"{location} 必须是对象")
            unknown = set(item) - {"title", "body", "source_ids", "suggested"}
            if unknown:
                raise DraftError(f"{location} 包含未知字段：{sorted(unknown)}")
            title = single_line(item.get("title"), f"{location}.title")
            title = single_line(title.rstrip("：: "), f"{location}.title")
            body = single_line(item.get("body"), f"{location}.body")
            ids = item.get("source_ids")
            if (not isinstance(ids, list) or not ids
                    or any(not isinstance(ref, str) or ref not in sources for ref in ids)):
                raise DraftError(f"{location}.source_ids 必须引用已提供的来源")
            suggested = item.get("suggested", False)
            if not isinstance(suggested, bool):
                raise DraftError(f"{location}.suggested 必须是布尔值")
            if suggested and key != "plans":
                raise DraftError("suggested 只用于下周计划")
            if suggested and "建议，待确认" not in title:
                title += "（建议，待确认）"
            heading = f"{number}. {title}"
            paragraphs.append((
                heading + "\n" + body,
                f"**{markdown_text(heading)}**\\\n{markdown_text(body)}",
                f"<strong>{escape(heading)}</strong><br>{escape(body)}",
                heading,
            ))
            source_rows.append(f"- {SECTION_LABELS[key]} / {number}. {title}：{', '.join(ids)}")
    spans = []
    offset = 0
    for text, _, _, heading in paragraphs:
        if heading is not None:
            spans.append({"start": offset, "end": offset + utf16_length(heading)})
        offset += utf16_length(text) + 2  # The two LF characters between paragraphs.
    return {
        "label": SECTION_LABELS[key],
        "text": "\n\n".join(part[0] for part in paragraphs),
        "markdown": "\n\n".join(part[1] for part in paragraphs),
        "html": '<p><br></p>'.join(f"<p>{part[2]}</p>" for part in paragraphs),
        "title_spans": spans,
    }, source_rows


def build_report(data):
    if not isinstance(data, dict):
        raise DraftError("输入必须是 JSON 对象")
    unknown = set(data) - {
        "period_start", "period_end", "generated_at", "feishu_url", "summary",
        "sources", "sections", "pending",
    }
    if unknown:
        raise DraftError(f"包含未知字段：{sorted(unknown)}")
    start = parse_date(data.get("period_start"), "period_start")
    end = parse_date(data.get("period_end"), "period_end")
    if end < start:
        raise DraftError("周期结束日期早于开始日期")
    timestamp = generated_time(data.get("generated_at"))
    url = data.get("feishu_url")
    if url is not None:
        single_line(url, "feishu_url")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.netloc or parsed.username
                or any(char.isspace() for char in url)):
            raise DraftError("feishu_url 必须是完整 HTTPS 链接，不含凭据或空白")
    summary = data.get("summary", "")
    if summary:
        summary = single_line(summary, "summary")
    elif not isinstance(summary, str):
        raise DraftError("summary 必须是字符串")
    sources = data.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise DraftError("sources 必须有来源；不能为空周编造周报")
    for source_id, reference in sources.items():
        if single_line(source_id, "来源 ID") != source_id:
            raise DraftError("来源 ID 不得有首尾空白")
        single_line(reference, f"sources.{source_id}")
    sections = data.get("sections")
    if not isinstance(sections, dict) or set(sections) != set(SECTION_LABELS):
        raise DraftError("sections 必须完整包含 progress、plans、coordination、other 四栏")
    pending = data.get("pending", [])
    if not isinstance(pending, list):
        raise DraftError("pending 必须是列表")
    pending = [single_line(value, "pending") for value in pending]
    fields = {}
    source_rows = []
    for key in SECTION_LABELS:
        fields[key], rows = render_field(key, sections[key], sources, summary)
        source_rows.extend(rows)
    report = [
        "# 工作周报", "",
        f"统计周期：{start.isoformat()} 至 {end.isoformat()}",
        f"生成时间：{timestamp:%Y-%m-%d %H:%M}（北京时间）",
        "状态：本地草稿，未提交",
        f"飞书周报链接：{url if url is not None else '待用户提供'}",
        "飞书处理状态：未填写，未核验", "",
    ]
    for field in fields.values():
        report.extend([f"## {field['label']}", "", field["markdown"], ""])
    report.extend([
        "---", "", "## 内部核对（不粘贴到飞书）", "",
        "### 来源索引", "",
        *[f"- {markdown_text(ref)}：{markdown_text(value)}" for ref, value in sources.items()],
        "", "### 工作项与来源", "", *source_rows,
        "", "### 待确认事项", "",
        *[f"- {markdown_text(value)}" for value in (pending or ["无"])],
        "", "### 飞书核验记录", "", "尚未执行页面填写或刷新核验。", "",
    ])
    payload = {
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "generated_at": timestamp.isoformat(), "feishu_url": url,
        "fields": fields,
    }
    return "\n".join(report), payload


def write_outputs(output, markdown, payload, replace=False):
    output = Path(output).absolute()
    if output.suffix != ".md":
        raise DraftError("--output 必须使用 .md 扩展名")
    companion = output.with_suffix(".fields.json")
    paths = (output, companion)
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise DraftError(f"拒绝写入符号链接或非普通文件：{path}")
    existing = [path for path in paths if path.exists()]
    if existing and not replace:
        raise DraftError("输出已存在；先合并人工修改，再用 --replace 备份后替换")
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if existing:
        archive = output.parent / "历史版本"
        archive.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(
            prefix=datetime.now(BEIJING).strftime("%Y%m%d_%H%M%S_"), dir=archive,
        ))
        for path in existing:
            shutil.copy2(path, backup / path.name)
    contents = (markdown, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    # Stage both files before publishing. Exclusive links also protect against a
    # new user file appearing after the initial existence check.
    with tempfile.TemporaryDirectory(prefix=".weekly-report-", dir=output.parent) as staging:
        staged = []
        for index, content in enumerate(contents):
            path = Path(staging) / str(index)
            path.write_text(content, encoding="utf-8")
            staged.append(path)
        for staged_path, target in zip(staged, paths):
            if replace:
                os.replace(staged_path, target)
            else:
                os.link(staged_path, target)
    return paths, backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="已核对的 UTF-8 事实 JSON")
    parser.add_argument("--output", type=Path, required=True, help="本地周报 .md 路径")
    parser.add_argument("--replace", action="store_true", help="备份已有本地文件后替换")
    args = parser.parse_args()
    try:
        outputs = (args.output.absolute(), args.output.absolute().with_suffix(".fields.json"))
        if args.input.resolve() in (path.resolve() for path in outputs):
            raise DraftError("输入事实文件不能与输出重名")
        data = json.loads(args.input.read_text(encoding="utf-8"))
        markdown, payload = build_report(data)
        paths, backup = write_outputs(args.output, markdown, payload, args.replace)
    except (ValueError, OSError) as error:
        parser.exit(2, f"生成失败：{error}\n")
    print(json.dumps({
        "markdown": str(paths[0]), "fields": str(paths[1]),
        "backup": str(backup) if backup else None,
        "status": "仅生成本地草稿；未填写飞书，未提交",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
