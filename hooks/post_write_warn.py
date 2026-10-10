#!/usr/bin/env python3
"""lhg-dashboard 硬门②：写看板规格 Markdown 后，若指标缺三件套则提醒。

触发：PostToolUse / Write|Edit。只检查本次写入的单个文件：
  - 非 .md 文件 → 直接放行；
  - 内容不像看板规格（含"指标体系"或"图表清单"关键词才判）→ 直接放行；
  - 指标体系表每行都有单位/时间范围/口径定义 → 放行；
  - 否则输出 additionalContext 提醒（不拦截，只提醒一次）。

逃生方式：LHG_DB_HOOKS_OFF=1（本次 hook 不触发）。
设计原则：只提醒、不拦截；内部出错 fail-open；零第三方依赖。
"""

import json
import os
import re
import sys


def table_rows(text, section):
    """取某章节内的 Markdown 表格数据行（跳过表头与分隔行）。"""
    lines = text.split("\n")
    in_sec, buf = False, []
    for ln in lines:
        if ln.startswith("## "):
            in_sec = ln[3:].strip() == section
            continue
        if in_sec and ln.strip().startswith("|"):
            buf.append(ln)
    rows = []
    for ln in buf:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if any(re.match(r"^:?-{3,}:?$", c) for c in cells):
            continue
        rows.append(cells)
    # 去掉表头行（第一行）
    return rows[1:] if rows else []


def main() -> int:
    if os.environ.get("LHG_DB_HOOKS_OFF") == "1":
        return 0

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0  # fail-open

    tool_input = payload.get("tool_input", {}) or {}
    path = tool_input.get("file_path", "") or ""
    if not path.lower().endswith((".md", ".markdown")):
        return 0

    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return 0  # 读不到文件：fail-open

    # 不像看板规格：不打扰
    if "指标体系" not in text and "图表清单" not in text:
        return 0

    problems = []
    for cells in table_rows(text, "指标体系"):
        if len(cells) < 4:
            continue
        name, unit, tr, defi = cells[0], cells[1], cells[2], cells[3]
        missing = []
        if not unit.strip() or unit.strip() in ("无", "-", "/"):
            missing.append("单位")
        if not tr.strip() or tr.strip() in ("无", "-", "/"):
            missing.append("时间范围")
        if len(defi.strip()) < 10:
            missing.append("口径定义")
        if missing:
            problems.append("指标「%s」缺：%s" % (name, "、".join(missing)))

    if not problems:
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "提醒：这份看板规格（%s）的指标三件套不全：%s。"
                "没有单位/时间范围/口径定义的数字无法解读，"
                "建议补齐后再交付；审计规则 DB-METRIC-001/002/003 会判 blocker/warning。"
                % (os.path.basename(path), "；".join(problems[:5]))
            ),
        }
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
