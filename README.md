# Weekly Report Assistant

[English](README.md) | [简体中文](README.zh-CN.md)

A Codex skill that turns your weekly work notes into a Chinese weekly report and fills a draft in the **Feishu Reports application** through an available, signed-in browser. You review the draft and submit it manually.

## What it does

- Collects work notes, explicitly referenced Codex chats, local records and supplied files, then groups related work by project rather than by day.
- Uses verifiable quantities, versions, deliverables and test results. Missing measurements remain unknown; it does not invent improvements or count earlier work again.
- Keeps four report sections in a fixed order, with numbered **bold titles on their own line, without a trailing colon**, followed by regular body text on the next line. There is no blank line between the title and body, and one blank line between items.
- Saves a local draft before editing Feishu, preserves your changes, and checks text, formatting and persistence after refreshing the page.
- Stops at a saved draft. It does not submit reports, change recipients or create a recurring automation.

The report headings are:

1. 本周工作/项目推进重点进展&总结 — Weekly progress and summary
2. 下周工作计划与安排 — Next week's plans
3. 需要协调与帮助 — Coordination and help needed
4. 其他需要同步的事项或说明 — Other updates

The first section starts with a short overview. Suggested plans are marked `建议，待确认` (suggestion, pending confirmation). The last two sections use `无` (none) only when you have confirmed there are no items; missing information is marked for confirmation.

## Requirements

- Codex with access to local skills and the supplied source material.
- Python 3.9 or later, with `Asia/Shanghai` timezone data, for the optional local rendering helper and tests. No third-party Python package is needed for either.
- For filling Feishu: a browser integration that can read and edit the signed-in report form, apply rich-text formatting and refresh the page. A search or scraping tool alone cannot do this. Browser integration is not bundled in this repository.

If the browser is unavailable or login is required, the skill retains the local draft and reports the remaining step. It does not claim that Feishu was updated. The Feishu Reports application is separate from Docx/Wiki; this workflow does not use `lark-cli` to read or fill reports.

## Install

Clone this repository directly into the default Codex skills directory. If that directory already contains this skill, update the existing installation instead of overwriting it.

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/zero-one-2024/weekly-report-assistant.git ~/.codex/skills/weekly-report-assistant
```

For a private repository, authenticate with GitHub before cloning. If you use a custom `CODEX_HOME`, use its `skills` directory instead. Start a new Codex chat to load the installed skill.

The owner's local installation may be a symbolic link to the development workspace. Keep that installation; a second clone is unnecessary.

## Use

Invoke the skill and supply the reporting period, work notes or references, and the current Feishu report link:

```text
Use $weekly-report-assistant to prepare my report for November 2–8, 2026
from the attached work notes. Fill the four sections at this Feishu report
link: <this week's link>. Save and verify the draft; do not submit.
```

You can also ask for a local draft without editing Feishu, or request a focused update while keeping the rest of an existing draft.

Dates use `Asia/Shanghai`. An explicit reporting period takes precedence over the default Monday–Sunday range. Only work completed by the generation time is treated as completed work. The default local archive is `/Users/dhl/WorkFile/13_工作周报` when it exists; otherwise the current working directory is used. Specify a different archive directory when needed. Applicable instructions in the destination directory are read before writing.

## Writing format

This is a **fictional formatting example**, not a record of actual work:

**1. 项目文档编制**\
完成2册独立手册，分别输出Word和PDF；监管方V1.0共10章、50页，项目方V1.0共8章、40页。

**2. 专项测试**\
执行20项用例，通过15项、失败3项、阻塞2项，形成3项待修复问题。

In Feishu, titles must be actual bold text, not visible Markdown asterisks. The browser workflow checks this after saving and refreshing.

## Local rendering helper

The helper converts an assistant-prepared JSON draft into Markdown and a separate four-field rich-text payload. You do not need to prepare JSON to use the skill. See the schema and fictional example in [Draft format](references/draft-format.md).

Run from the repository root:

```bash
python3 scripts/render_report.py /absolute/path/facts.json --output /absolute/path/reports/2026-11-02_2026-11-08_工作周报.md
```

It produces the requested `.md` file and a companion `.fields.json` containing plain text, Markdown, HTML and UTF-16 title selection offsets for each field. Source notes and internal checks stay out of the four-field payload.

Existing outputs are protected by default. After merging your latest edits into the input, add `--replace` to back up both old outputs under `历史版本/` before replacing them. This only replaces local files; it does not authorize overwriting a remote report.

The helper validates structure and source references, not whether a source supports a claim or whether a number is true. It never connects to Feishu. Initial output is always marked as a local, unverified draft; actual browser verification must be recorded separately.

## Tests and verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The suite covers all four fields, bold titles on their own line with no trailing colon, body text on the next line, blank-line spacing, HTML escaping, UTF-16 offsets, source references, Beijing time conversion, exact URL preservation, overwrite protection, backups and CLI behavior. It also executes the JSON example in the documentation.

These are local tests. A real Feishu editing session is still needed to verify browser access, editor behavior and draft persistence. No test accesses or submits a real report.

## Repository layout

```text
SKILL.md                         Skill entry point and writing rules
agents/openai.yaml               Codex skill metadata
references/browser-workflow.md  Browser editing and verification procedure
references/draft-format.md      Local input schema and helper usage
scripts/render_report.py        Local draft renderer
tests/test_render_report.py     Standalone tests
```

The skill instructions and references are written in Chinese to match the report-writing workflow. Both README files describe the same capabilities and limits.
