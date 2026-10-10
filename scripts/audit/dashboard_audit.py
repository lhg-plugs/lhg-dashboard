#!/usr/bin/env python3
"""lhg-dashboard 确定性看板规范审计：12 条规则，零第三方依赖。

扫描目标目录的 Markdown 看板规格说明（约定见下），输出 JSON 与人类可读报告；
有 blocker 时以 exit code 2 退出，warning 仅以 exit code 1 退出（供 hooks 与 CI 使用）。

用法：
    python3 dashboard_audit.py --target <目录> [--format json|text] [--fail-on blocker|warning|nit]

看板规格 Markdown 约定（审计脚本识别的结构）：
  - `# 标题`
  - `## 指标体系`：Markdown 表格，列含「指标 / 单位 / 时间范围 / 口径定义」
  - `## 图表清单`：Markdown 表格，列含「编号 / 标题 / 图表类型 / 数据维度 / 说明」
  - `## 色板`：十六进制颜色（如 #5470C6），每行一个或顿号分隔
  - `## 布局`：含「一屏图表数：N」声明（缺省时按图表清单行数计）

规则文案均为本项目原创（思路受数据可视化常识启发，
未复制任何第三方原文）。
"""

import argparse
import json
import os
import re
import sys

VERSION = "1.0.0"
TOOL = "lhg-dashboard/dashboard-audit"

SKIP_DIRS = {".git", "__pycache__", "node_modules", "dist", "build", "vendor"}
SCAN_EXTS = {".md", ".markdown"}

SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
HEX_COLOR_RE = re.compile(r"#([0-9a-fA-F]{6})\b")
COUNT_RE = re.compile(r"(\d+)\s*个?\s*(分类|类别|类目|分组|分段)")
SCREEN_COUNT_RE = re.compile(r"一屏图表数[：:]\s*(\d+)")
TIME_WORDS = ["时序", "日期", "时间", "趋势"]
PIE_TYPES = ["饼图", "环形图", "pie"]
MAX_PIE_SLICES = 6
MAX_SCREEN_CHARTS = 8


class Finding:
    __slots__ = ("rule_id", "severity", "category", "title", "file", "line",
                 "snippet", "detail")

    def __init__(self, rule_id, severity, category, title, file, line,
                 snippet, detail):
        self.rule_id = rule_id
        self.severity = severity
        self.category = category
        self.title = title
        self.file = file
        self.line = line
        self.snippet = snippet
        self.detail = detail

    def to_dict(self):
        return {
            "rule_id": self.rule_id, "severity": self.severity,
            "category": self.category, "title": self.title,
            "file": self.file, "line": self.line,
            "snippet": self.snippet, "detail": self.detail,
        }


class Doc:
    """解析后的看板规格文档。"""

    def __init__(self, path, lines):
        self.path = path
        self.lines = lines
        self.sections = {}
        self._parse()

    def _parse(self):
        cur = None
        for i, ln in enumerate(self.lines):
            m = SECTION_RE.match(ln)
            if m:
                cur = m.group(1).strip()
                continue
            if cur is not None:
                self.sections.setdefault(cur, []).append((i + 1, ln))

    def section_lines(self, name):
        return self.sections.get(name, [])

    def section_text(self, name):
        return "\n".join(ln for _, ln in self.sections.get(name, []))


def parse_tables(section_lines):
    """解析一个章节内的 Markdown 表格，返回 [({"列名": (行号, 值)}, ...)]。"""
    tables = []
    buf = []
    for lineno, ln in section_lines:
        if ln.strip().startswith("|"):
            buf.append((lineno, ln))
        else:
            if buf:
                tables.append(buf)
                buf = []
    if buf:
        tables.append(buf)
    out = []
    for buf in tables:
        rows = [[c.strip() for c in ln.strip().strip("|").split("|")]
                for _, ln in buf]
        if len(rows) < 2:
            continue
        header = rows[0]
        if not any(re.match(r"^:?-{3,}:?$", c) for c in rows[1]):
            continue  # 第二行不是分隔行：不是表格
        for k, ln in zip([l for l, _ in buf[2:]], rows[2:]):
            out.append({h: (k, v) for h, v in zip(header, ln)})
    return out


def col(row, *names):
    for n in names:
        if n in row:
            return row[n]
    return (0, "")


def collect_files(target):
    out = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            _, ext = os.path.splitext(f)
            if ext.lower() in SCAN_EXTS:
                out.append(os.path.join(root, f))
    return sorted(out)


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(path, encoding="gbk", errors="ignore") as f:
            return f.read()


def hex_to_hsv(h):
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        hue = 0.0
    elif mx == r:
        hue = (60 * ((g - b) / d) + 360) % 360
    elif mx == g:
        hue = 60 * ((b - r) / d) + 120
    else:
        hue = 60 * ((r - g) / d) + 240
    sat = 0.0 if mx == 0 else d / mx
    return hue, sat, mx


def palette_colors(doc):
    return ["#" + m.group(1) for m in
            HEX_COLOR_RE.finditer(doc.section_text("色板"))]


def palette_has_red_green(colors):
    red = green = False
    for c in colors:
        h, s, v = hex_to_hsv(c[1:])
        if s > 0.35 and v > 0.3:
            if h < 15 or h > 345:
                red = True
            elif 80 < h < 160:
                green = True
    return red and green


# ---------------- 规则注册表 ----------------
RULES = []


def rule(rule_id, severity, category, title):
    def deco(cls):
        inst = cls()
        r = Rule(rule_id, severity, category, title)
        r.check = inst.check
        RULES.append(r)
        return cls
    return deco


class Rule:
    def __init__(self, rule_id, severity, category, title):
        self.rule_id = rule_id
        self.severity = severity
        self.category = category
        self.title = title

    def check(self, doc, findings):
        raise NotImplementedError


@rule("DB-CHART-001", "blocker", "图表选型", "饼图分类过多")
class ChartPieSlices:
    def check(self, doc, findings):
        for row in parse_tables(doc.section_lines("图表清单")):
            ctype = col(row, "图表类型", "类型")[1]
            dim = col(row, "数据维度", "维度")[1]
            note = col(row, "说明")[1]
            title = col(row, "标题")[1]
            code_c = col(row, "编号")
            code, codeln = code_c[1], code_c[0]
            if any(p in ctype for p in PIE_TYPES):
                m = COUNT_RE.search(dim + " " + note)
                if m and int(m.group(1)) > MAX_PIE_SLICES:
                    findings.append(Finding(
                        "DB-CHART-001", "blocker", "图表选型",
                        "饼图分类过多", doc.path, codeln,
                        "%s：%s（%s 个分类）" % (code, title, m.group(1)),
                        "饼图「%s」有 %s 个分类，超过 %d 个上限。"
                        "人眼无法分辨过多扇区，请改用条形图，或只保留 Top5、其余并入「其他」。"
                        % (title or code, m.group(1), MAX_PIE_SLICES)))


@rule("DB-CHART-002", "warning", "图表选型", "折线图用于非时序数据")
class ChartLineNonTime:
    def check(self, doc, findings):
        for row in parse_tables(doc.section_lines("图表清单")):
            _, ctype = col(row, "图表类型", "类型")
            _, dim = col(row, "数据维度", "维度")
            _, title = col(row, "标题")
            _, code = col(row, "编号")
            codeln = col(row, "编号")[0]
            if "折线" in ctype and not any(w in dim for w in TIME_WORDS):
                findings.append(Finding(
                    "DB-CHART-002", "warning", "图表选型",
                    "折线图用于非时序数据", doc.path, codeln,
                    "%s：%s" % (code, title),
                    "折线图「%s」的数据维度是「%s」，不含时序信息。"
                    "折线图的斜率天然暗示趋势，非时序数据用折线会误导读者，请改用柱状图/条形图。"
                    % (title or code, dim)))


@rule("DB-CHART-003", "warning", "图表选型", "双轴图表误导风险")
class ChartDualAxis:
    def check(self, doc, findings):
        for row in parse_tables(doc.section_lines("图表清单")):
            _, ctype = col(row, "图表类型", "类型")
            _, title = col(row, "标题")
            _, code = col(row, "编号")
            codeln = col(row, "编号")[0]
            if "双轴" in ctype:
                findings.append(Finding(
                    "DB-CHART-003", "warning", "图表选型",
                    "双轴图表误导风险", doc.path, codeln,
                    "%s：%s" % (code, title),
                    "双轴图「%s」的左右刻度可任意缩放，容易制造虚假的相关性。"
                    "如非用不可：必须在图注中写明双轴刻度起点；优先考虑拆成两张图。"
                    % (title or code)))


@rule("DB-CHART-004", "warning", "图表选型", "3D 图表误导风险")
class Chart3D:
    def check(self, doc, findings):
        for row in parse_tables(doc.section_lines("图表清单")):
            _, ctype = col(row, "图表类型", "类型")
            _, title = col(row, "标题")
            _, code = col(row, "编号")
            codeln = col(row, "编号")[0]
            if "3D" in ctype or "三维" in ctype:
                findings.append(Finding(
                    "DB-CHART-004", "warning", "图表选型",
                    "3D 图表误导风险", doc.path, codeln,
                    "%s：%s" % (code, title),
                    "3D 图表「%s」的透视会扭曲真实比例（前排扇区显大）。"
                    "数据看板一律用 2D，3D 只留给装饰性场合。" % (title or code)))


@rule("DB-COLOR-001", "warning", "色板", "色板存在红绿搭配（色盲不友好）")
class ColorRedGreen:
    def check(self, doc, findings):
        colors = palette_colors(doc)
        if len(colors) >= 2 and palette_has_red_green(colors):
            findings.append(Finding(
                "DB-COLOR-001", "warning", "色板",
                "色板存在红绿搭配（色盲不友好）", doc.path, 0,
                "、".join(colors[:6]),
                "色板同时含有红色系与绿色系颜色，红绿色盲（最常见色盲类型）"
                "无法区分二者。请换用色盲友好色板（如 Okabe-Ito：#E69F00、#56B4E9、"
                "#009E73、#0072B2），或用形状/纹理辅助区分。"))


@rule("DB-COLOR-002", "warning", "色板", "色板颜色数量少于最多分类数")
class ColorNotEnough:
    def check(self, doc, findings):
        colors = palette_colors(doc)
        if not colors:
            return
        max_n = 0
        for row in parse_tables(doc.section_lines("图表清单")):
            _, ctype = col(row, "图表类型", "类型")
            _, dim = col(row, "数据维度", "维度")
            _, note = col(row, "说明")
            if any(p in ctype for p in PIE_TYPES):
                m = COUNT_RE.search(dim + " " + note)
                if m:
                    max_n = max(max_n, int(m.group(1)))
        if max_n > len(colors):
            findings.append(Finding(
                "DB-COLOR-002", "warning", "色板",
                "色板颜色数量少于最多分类数", doc.path, 0,
                "色板 %d 色 / 最多分类 %d" % (len(colors), max_n),
                "有图表需要 %d 个可区分颜色，但色板只有 %d 个，"
                "多出的分类会复用颜色导致误读。请补充色板颜色。" % (max_n, len(colors))))


@rule("DB-METRIC-001", "blocker", "指标", "指标缺少单位")
class MetricUnit:
    def check(self, doc, findings):
        for row in parse_tables(doc.section_lines("指标体系")):
            _, name = col(row, "指标", "指标名称", "名称")
            lineno, unit = col(row, "单位")
            if not unit.strip() or unit.strip() in ("无", "-", "/"):
                findings.append(Finding(
                    "DB-METRIC-001", "blocker", "指标",
                    "指标缺少单位", doc.path, lineno, name,
                    "指标「%s」没有标注单位。没有单位的数字无法解读"
                    "（是元还是万元？是人还是人次？），请补上。" % name))


@rule("DB-METRIC-002", "blocker", "指标", "指标缺少时间范围")
class MetricTimeRange:
    def check(self, doc, findings):
        for row in parse_tables(doc.section_lines("指标体系")):
            _, name = col(row, "指标", "指标名称", "名称")
            lineno, tr = col(row, "时间范围", "统计周期", "周期")
            if not tr.strip() or tr.strip() in ("无", "-", "/"):
                findings.append(Finding(
                    "DB-METRIC-002", "blocker", "指标",
                    "指标缺少时间范围", doc.path, lineno, name,
                    "指标「%s」没有标注时间范围。脱离时间范围的指标"
                    "无法判断是日环比还是年累计，请补上（如 2026-10-01~2026-10-11）。"
                    % name))


@rule("DB-METRIC-003", "warning", "指标", "指标口径未定义")
class MetricDef:
    def check(self, doc, findings):
        for row in parse_tables(doc.section_lines("指标体系")):
            _, name = col(row, "指标", "指标名称", "名称")
            lineno, defi = col(row, "口径定义", "口径", "定义")
            if len(defi.strip()) < 10:
                findings.append(Finding(
                    "DB-METRIC-003", "warning", "指标",
                    "指标口径未定义", doc.path, lineno, name,
                    "指标「%s」的口径定义缺失或不足 10 字。"
                    "同一指标不同人口径可能差一个数量级（如是否含退款、是否含税），"
                    "请写清统计口径。" % name))


@rule("DB-LAYOUT-001", "warning", "布局", "一屏图表过多")
class LayoutTooMany:
    def check(self, doc, findings):
        charts = parse_tables(doc.section_lines("图表清单"))
        n = len(charts)
        m = SCREEN_COUNT_RE.search(doc.section_text("布局"))
        if m:
            n = int(m.group(1))
        if n > MAX_SCREEN_CHARTS:
            findings.append(Finding(
                "DB-LAYOUT-001", "warning", "布局",
                "一屏图表过多", doc.path, 0, "一屏 %d 张图表" % n,
                "一屏放 %d 张图表，超过 %d 张上限，信息过载等于没有重点。"
                "请按「核心指标置顶、明细下钻」的原则拆成多屏或分页。" % (
                    n, MAX_SCREEN_CHARTS)))


@rule("DB-LAYOUT-002", "warning", "布局", "图表缺少标题")
class LayoutNoTitle:
    def check(self, doc, findings):
        for row in parse_tables(doc.section_lines("图表清单")):
            _, title = col(row, "标题")
            _, code = col(row, "编号")
            codeln = col(row, "编号")[0]
            if not title.strip():
                findings.append(Finding(
                    "DB-LAYOUT-002", "warning", "布局",
                    "图表缺少标题", doc.path, codeln,
                    code or "（未编号图表）",
                    "图表 %s 没有标题。无标题图表等于让读者猜它在讲什么，"
                    "请补上「主语 + 指标 + 时间」式标题（如：各渠道销售额占比-10月）。"
                    % (code or "（未编号）")))


@rule("DB-SCALE-001", "warning", "刻度", "柱状图 Y 轴截断")
class ScaleTruncated:
    def check(self, doc, findings):
        for row in parse_tables(doc.section_lines("图表清单")):
            _, ctype = col(row, "图表类型", "类型")
            _, note = col(row, "说明")
            _, title = col(row, "标题")
            _, code = col(row, "编号")
            codeln = col(row, "编号")[0]
            if ("柱状" in ctype or "条形" in ctype) and re.search(
                    r"截断|不从 ?0 ?开始|从 ?[1-9]\d* ?开始|Y ?轴.{0,6}不为 ?0",
                    note):
                findings.append(Finding(
                    "DB-SCALE-001", "warning", "刻度",
                    "柱状图 Y 轴截断", doc.path, codeln,
                    "%s：%s" % (code, title),
                    "柱状图「%s」的 Y 轴被截断（%s）。"
                    "柱长代表数值，截断 Y 轴会夸大差异，请从 0 开始；"
                    "差异实在太小请改用表格呈现精确值。" % (
                        title or code, note.strip()[:30])))


def audit_file(path):
    text = read_text(path)
    doc = Doc(path, text.split("\n"))
    findings = []
    for r in RULES:
        try:
            r.check(doc, findings)
        except Exception as e:  # 单条规则出错不影响整体：fail-open 思想
            sys.stderr.write("rule %s error: %s\n" % (r.rule_id, e))
    return findings


def main():
    ap = argparse.ArgumentParser(description="lhg-dashboard 看板规范审计")
    ap.add_argument("--target", required=True, help="看板规格目录")
    ap.add_argument("--format", choices=["json", "text"], default="text")
    ap.add_argument("--fail-on", choices=["blocker", "warning", "nit"],
                    default="nit",
                    help="低于此级别的发现不影响退出码（默认 nit，即 warning 也会以 exit 1 退出）")
    args = ap.parse_args()

    files = collect_files(args.target)
    findings = []
    for p in files:
        findings.extend(audit_file(p))

    order = {"blocker": 0, "warning": 1, "nit": 2}
    findings.sort(key=lambda f: (order[f.severity], f.file, f.line))

    summary = {
        "tool": TOOL, "version": VERSION,
        "files_scanned": len(files),
        "rules_run": len(RULES),
        "blocker": sum(1 for f in findings if f.severity == "blocker"),
        "warning": sum(1 for f in findings if f.severity == "warning"),
        "nit": sum(1 for f in findings if f.severity == "nit"),
    }

    if args.format == "json":
        print(json.dumps({"summary": summary,
                          "findings": [f.to_dict() for f in findings]},
                         ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print("看板规范审计报告  (%s)" % TOOL)
        print("扫描文件：%d  规则数：%d" % (
            summary["files_scanned"], summary["rules_run"]))
        print("blocker：%d  warning：%d  nit：%d" % (
            summary["blocker"], summary["warning"], summary["nit"]))
        print("=" * 60)
        for f in findings:
            print("[%s] %s  %s:%s" % (
                f.severity.upper(), f.rule_id, f.file, f.line or "-"))
            print("  %s" % f.title)
            print("  %s" % f.detail.replace("\n", " ")[:160])

    sev_exit = {"blocker": 2, "warning": 1, "nit": 0}
    threshold = {"blocker": 2, "warning": 1, "nit": 0}[args.fail_on]
    code = 0
    for f in findings:
        if sev_exit[f.severity] >= threshold:
            code = max(code, sev_exit[f.severity])
    sys.exit(code)


if __name__ == "__main__":
    main()
