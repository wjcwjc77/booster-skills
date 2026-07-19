---
name: Booster Strategy Self-Evolve
description: "Automated strategy iteration for Booster 3v3 soccer agent. Runs batch matches against Booster AI, analyzes match logs (goals, penalties, positioning), and provides targeted strategy improvement suggestions based on official documentation. Use when: want to test strategy, run matches, analyze results, improve tactics, iterate on soccer agent."
argument-hint: "[runs] [speed]"
arguments: [runs, speed]
allowed-tools: Bash Read Glob Grep AskUserQuestion Agent
---

# Booster Strategy Self-Evolve

You are a strategy iteration assistant for the Booster 3v3 robot soccer simulation. Your job is to **autonomously run matches, analyze results, and suggest strategy improvements**.

## Workflow

### Phase 1: Confirm Parameters with User

Ask the user:
1. **How many matches** to run (default: 5, recommend 5-10 for statistical significance)
2. **Simulation speed** (default: 3.0x)
3. **Whether the agent has been built and deployed** to the Docker container via Booster Studio

If arguments were provided: first arg = runs, second arg = speed. Skip asking if already specified.

Do NOT proceed until the user confirms deployment is complete.

### Phase 2: Run Batch Matches

Execute the batch eval script bundled with this skill:
```bash
python3 .claude/skills/booster-strategy-self-evolve/scripts/batch_eval.py --runs <N> --tag <descriptive-tag> --speed <speed>
```

The script will:
- Start matches via Game Controller HTTP API (port 38383 in container `my26v-k1`)
- Wait for each match to complete (10 min sim time)
- Parse events.jsonl for goals, penalties, stuck events
- Write results to `logs/batch/<tag>/<timestamp>/`

Monitor progress and report intermediate results to the user as matches complete.

### Phase 3: Analyze Results

After all matches finish, analyze:

1. **Win/Loss/Draw record and win rate**
2. **Goal patterns**: when goals are scored (early/late), frequency
3. **Defensive issues**: goals conceded patterns, penalty types
4. **Per-match breakdown**: identify the worst match and what went wrong
5. **Comparison with previous runs** (check `logs/batch/` for historical data)

Read the match JSON files for detailed analysis:
- `run_NNN.json`: score, goal timings, penalties, stuck counts
- `run_NNN_decisions.jsonl` (if present): strategy decision trace

### Phase 4: Strategy Suggestions

Based on the analysis, provide **specific, actionable** strategy modifications. Reference the official documentation below for valid parameters and approaches.

Structure suggestions as:
- **What to change** (specific file, parameter, or logic)
- **Why** (what match pattern this addresses)
- **Expected impact** (which metric should improve)
- **Risk** (what could get worse)

---

## Project Structure (Typical Booster 3v3 Agent)

```
src/
├── soccer_framework/config.py    ← Tuning parameters (SoccerStrategyTuning)
├── play/
│   ├── playbook.py               ← Role assignment (who does what)
│   ├── default_roles.py          ← Role behaviors (chaser, supporter, goalkeeper)
│   └── nodes.py                  ← BT leaf nodes
├── tactics/
│   ├── targeting/attack.py       ← Kick target selection (pass/shot/dribble)
│   ├── targeting/support.py      ← Supporter positioning
│   ├── motion.py                 ← Motion controller + kick power
│   ├── navigation.py             ← Obstacle avoidance
│   └── ready_stance.py           ← Set-play positioning
└── behavior_tree/                ← BT infrastructure
```

## Bundled Scripts

This skill includes the following scripts in its `scripts/` directory:

| Script | Description |
|---|---|
| `scripts/batch_eval.py` | Batch match runner — starts matches via GC API, collects results |
| `scripts/set_speed.sh` | Simulation speed control via WebSocket |

## Key Tuning Parameters (src/soccer_framework/config.py)

| Parameter | Description | Safe Range |
|---|---|---|
| max_linear_speed | Robot movement speed (m/s) | 0.8 - 1.5 |
| max_angular_speed | Turn speed (rad/s) | 1.0 - 1.5 |
| soccer_kick_power | Base kick force | 1.0 - 10.0 |
| soccer_kick_enter_distance | Distance to enter kick mode | 1.5 - 3.0 |
| pass_enabled | Enable/disable passing | true/false |
| pass_min_score | Pass quality threshold | 0.40 - 0.65 |
| dribble_advance_m | Dribble distance per kick | 0.8 - 2.0 |
| support_depth_m | Supporter distance behind ball | 0.8 - 2.0 |

## Opponent Analysis (Booster AI)

The opponent (com.booster.default3v3ai) is the platform's built-in AI. Key characteristics:
- **Speed**: 0.8 m/s linear, 1.0 rad/s angular (slower than most custom agents)
- **Kick power**: 1.5 (relatively weak)
- **Chaser selection**: Pure distance-based (no ETA/speed awareness)
- **Passing**: Only passes forward (min 0.35m gain), lane_clear threshold 0.52
- **Dribble pattern**: Always toward center (y * 0.65), advance 1.15m
- **Goalkeeper**: Only challenges when ball is in defensive area (x < -field_length*0.18 in their frame). When passive, tracks y with only 0.38 factor.
- **Shot target**: Always aims at goal center (y=0)
- **Source location in container**: `/opt/booster/booster_agent_data/data/agents/extract/com.booster.default3v3ai/agent/com_booster_default3v3ai/lib/python3.10/site-packages/com_booster_default3v3ai/`

## Official Reference Documentation

All official platform documentation is included in the `references/` directory alongside this skill. Read these files for rules, parameters, and tactical guidance:

| File | Description |
|---|---|
| `references/INDEX.md` | Document index and overview |
| `references/field_rules.md` | Field dimensions, goal/penalty rules, scoring conditions |
| `references/config_params.md` | All tunable parameters with safe ranges and descriptions |
| `references/competition_rules.md` | Match rules, penalties, set-play rules |
| `references/tactic_design.md` | Tactical design principles and patterns |
| `references/role_assignment.md` | How to assign roles (chaser/supporter/goalkeeper) |
| `references/shooter_selection.md` | Shooter selection and kick target logic |
| `references/movement_optimization.md` | Movement speed, avoidance, path planning |
| `references/behavior_tree.md` | Behavior tree structure and node types |
| `references/example_strategy.md` | Example strategy implementations |

When making strategy suggestions, **always reference the relevant documentation** to justify parameter ranges and design choices. Read the specific file when you need detailed information.

## Historical Strategy Iterations

Check the project's `docs/references/changelog/` directory (if it exists) for context on what has been tried and what worked/failed:
- Successful approaches are documented with their improvements
- Failed approaches document why they regressed

If no changelog exists, start fresh and document your iterations.

## Match Analysis Checklist

When analyzing results, check for these common issues:

1. **Low shot accuracy**: Robot kicks while misaligned → increase approach_offset or add alignment delay
2. **GK conceding easy goals**: Check if opponent shooting pattern is exploitable (they always shoot center!)
3. **Ball stuck / dropped ball**: Usually means both teams contesting without progress → check obstacle avoidance
4. **Penalties (fallenRobot)**: Speed too high for stability → reduce max_linear_speed
5. **Penalties (illegalPosition)**: Set-play avoidance distance too small → increase opponent_restart_avoid_distance_m
6. **Low scoring**: Check if shot threshold is too high or dribble is aimless → review kick target priority
7. **Opponent scores on counter**: Interceptor/supporter not blocking → review defensive positioning
