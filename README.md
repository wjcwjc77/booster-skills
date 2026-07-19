# Booster Strategy Self-Evolve

Automated strategy iteration skill for Booster 3v3 robot soccer simulation. Runs batch matches against Booster AI, analyzes match logs (goals, penalties, positioning), and provides targeted strategy improvement suggestions.

## Usage

将 `booster-strategy-self-evolve` 目录复制到你的项目 `.claude/skills/` 下：

```bash
cp -r booster-strategy-self-evolve /path/to/your-project/.claude/skills/
```

目录结构应为：

```
your-project/
└── .claude/
    └── skills/
        └── booster-strategy-self-evolve/
            ├── SKILL.md
            ├── scripts/
            │   ├── batch_eval.py
            │   └── set_speed.sh
            └── references/
                ├── INDEX.md
                ├── field_rules.md
                ├── config_params.md
                ├── competition_rules.md
                ├── tactic_design.md
                ├── role_assignment.md
                ├── shooter_selection.md
                ├── movement_optimization.md
                ├── behavior_tree.md
                └── example_strategy.md
```

放置完成后，在 Claude Code 中即可通过 `/booster-strategy-self-evolve` 触发自动策略迭代流程。

## Workflow

1. **确认参数** — 比赛场次、仿真速度、是否已部署
2. **批量比赛** — 通过 Game Controller HTTP API 自动启动并收集结果
3. **分析结果** — 胜率、进球模式、失球模式、罚球统计
4. **策略建议** — 基于数据给出具体参数调整建议

## Prerequisites

- Docker 容器 `my26v-k1` 正在运行
- Agent 已通过 Booster Studio 构建并部署到容器中
- Python 3.10+
