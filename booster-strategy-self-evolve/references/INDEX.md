# Feishu Reference Index

Generated: 2026-07-10

| File | Tag | Summary | Suggested Use |
| --- | --- | --- | --- |
| `competition_rules.md` | `rules` | Explains the tournament format, ranking, tiebreakers, and advancement logic. | Use when deciding whether to optimize for goal difference, foul reduction, or risk level across iterations. |
| `field_rules.md` | `rules` | Defines match flow, restart timing, valid goals, restart fouls, inactivity penalties, and positioning constraints. | Use before changing kickoff, restart, goalkeeper, or aggressive chase behavior that could trigger penalties. |
| `example_strategy.md` | `deep-dive` | Walks through the sample project structure, core modules, extension points, and common tactical customization entry points. | Use as the map of where to modify strategy code safely. |
| `tactic_design.md` | `deep-dive` | Explains the tactical layer around `Playbook.assign_roles`, role responsibilities, and macro role allocation ideas. | Use when redesigning attacker/supporter/goalkeeper coordination or adding new role logic. |
| `behavior_tree.md` | `deep-dive` | Describes the match-state behavior tree, blackboard flow, role dispatch, and execution order. | Use when strategy changes require BT-level restructuring instead of parameter tuning. |
| `role_assignment.md` | `quick-opt` | Focuses on dynamic role assignment, priority preservation, and how to handle reduced-player edge cases. | Use when changing `assign_roles` or deciding when to drop supporter roles under pressure. |
| `shooter_selection.md` | `quick-opt` | Details chaser selection, shot/pass/dribble decision order, and the parameters that affect attacking decisions. | Use when tuning `select_chaser`, kick target logic, or pass/dribble thresholds. |
| `movement_optimization.md` | `quick-opt` | Breaks down avoidance, motion control, yaw avoidance, and the main parameters that affect movement smoothness. | Use when logs show jitter, over-avoidance, collisions, or slow ball approach. |
| `config_params.md` | `quick-opt` | Lists the major `SoccerStrategyTuning` parameters, their intent, safe ranges, and scenario-oriented tuning advice. | Use as the primary parameter reference before any numeric change to avoid direction mistakes. |
