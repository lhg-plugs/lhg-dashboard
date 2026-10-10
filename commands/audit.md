---
description: 看板规格体检：跑确定性审计脚本 + 人工评审清单，输出 blocker/warning/nit 分级报告。
argument-hint: [规格目录] [--fix]
---

# /lhg-dashboard:audit —— 看板规范体检

用户输入在 `$ARGUMENTS` 中：`[规格目录] [--fix]`。目录缺省为当前目录。

## 流程（加载 lhg-db-audit 全程指导）

### 1. 跑脚本

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/audit/dashboard_audit.py --target <目录> --format text
```

记下 blocker / warning / nit 的数量与明细（含文件、行号、规则说明）。

### 2. 分级与修复

- blocker：逐条修。`--fix` 缺省即自动修（最小改动原则）；修一条用脚本复查一条。
- 修不掉的（如需用户确认真实口径、数据源字段）→ 列入"需人工处理"，不许为清零改规则。
- warning：修掉或写豁免理由；nit：顺手修。

### 3. 人工评审

按 `lhg-db-audit` 第 5 节清单执行脚本抓不到的项：

- 指标可行动性：这个指标变了，有人知道该干什么吗？（没人行动的指标是装饰）
- 图表诚实性：有没有用截断 Y 轴、双轴、3D 夸大/缩小差异？
- 标题诚实性：标题说的是图里实际画的东西吗？
- 色板现实性：在投影仪/手机小屏上还分得清吗？

### 4. 输出报告

按 `lhg-db-audit` 第 6 节模板输出自查报告，结论写清"通过 / 不通过"。

## 硬规则

- 有 blocker 就说"通过" → 不许。
- 为清零改脚本规则或阈值 → 这是作弊，不许。
- 规格里出现编造的数据源字段 → 标 blocker，打回核实。
