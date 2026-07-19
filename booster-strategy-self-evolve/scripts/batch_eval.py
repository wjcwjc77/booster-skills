#!/usr/bin/env python3
"""
Batch match runner + result aggregator for the 3v3 soccer agent.

Purpose
-------
Single-match results in this environment are dominated by variance (goal-diff
std easily ±1.5 between identical runs). Any strategy A/B comparison must be
done on **batches**, not single games. This script drives the Game Controller
HTTP API, waits for each match to finish, parses events.jsonl, writes one JSON
row per match, and prints aggregate stats at the end.

Prerequisites (this script does NOT handle these — the human does)
------------------------------------------------------------------
1. `my26v-k1` container is running (`docker ps` shows it "Up").
2. The Agent under test has been deployed via Booster Studio's Run button.
3. `agent_1` is the team under test; the opponent is whatever GC has configured.

Usage
-----
    python3 scripts/batch_eval.py --runs 10 --tag v6-baseline
    python3 scripts/batch_eval.py --runs 3  --tag smoke --speed 3.0

Output
------
    logs/batch/<tag>/<timestamp>/
      run_001.json .. run_N.json    # one row per match
      summary.json                  # aggregated stats
      summary.txt                   # human-readable summary

Design notes
------------
- stdlib only for the happy path. WS speed control is optional; if the
  `websockets` package is missing we warn and continue at 1x.
- events.jsonl is a container-lifetime append-only log. We record its line
  count before each match and slice new lines after `state=finished` so cross-
  match boundaries are clean without needing a GC reset endpoint.
- Reset-between-matches: GC transitions finished → initial on its own on this
  build; if it does not, `POST /match/start` returns an error and the script
  aborts loudly instead of silently reusing the previous match's state.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONTAINER = "my26v-k1"
GC_PORT_INSIDE = "38383"
PHYS_PORT_INSIDE = "8788"
EVENTS_PATH_IN_CONTAINER = (
    "/usr/local/booster_robot/booster_robocup_sim/logs/game-control/events.jsonl"
)
DECISIONS_DIR_IN_CONTAINER = "/tmp/soccer_decisions"
POLL_INTERVAL_S = 5.0
MAX_MATCH_WALL_S = 60 * 60  # give up on a single match after 1 real-time hour


# ---------------------------------------------------------------------------
# Docker / HTTP helpers
# ---------------------------------------------------------------------------


def docker_port(container: str, port_inside: str) -> str:
    """Return host port bound to <container>:<port_inside>. Raises on miss."""
    out = subprocess.check_output(
        ["docker", "port", container, port_inside], text=True
    ).strip()
    if not out:
        raise RuntimeError(
            f"docker port {container} {port_inside} returned empty — is the container running?"
        )
    # e.g. "0.0.0.0:55142" or "[::]:55142\n0.0.0.0:55142"
    for line in out.splitlines():
        if ":" in line:
            return line.rsplit(":", 1)[1].strip()
    raise RuntimeError(f"could not parse docker port output: {out!r}")


def http_get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"raw": raw}


def docker_exec_capture(container: str, argv: list[str]) -> str:
    return subprocess.check_output(["docker", "exec", container, *argv], text=True)


def events_line_count(container: str) -> int:
    """wc -l on events.jsonl inside the container. Returns 0 if file missing."""
    try:
        out = docker_exec_capture(container, ["wc", "-l", EVENTS_PATH_IN_CONTAINER])
    except subprocess.CalledProcessError:
        return 0
    return int(out.strip().split()[0])


def events_slice(container: str, from_line: int, to_line: int) -> list[dict[str, Any]]:
    """Return event dicts from (from_line, to_line] using sed."""
    if to_line <= from_line:
        return []
    start = from_line + 1
    out = docker_exec_capture(
        container,
        ["sed", "-n", f"{start},{to_line}p", EVENTS_PATH_IN_CONTAINER],
    )
    rows: list[dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


# ---------------------------------------------------------------------------
# Decision-log slicing
# ---------------------------------------------------------------------------


def list_decision_files(container: str) -> list[str]:
    """List *.jsonl files under DECISIONS_DIR_IN_CONTAINER (may be empty)."""
    try:
        out = docker_exec_capture(
            container,
            ["sh", "-c", f"ls -1 {DECISIONS_DIR_IN_CONTAINER}/*.jsonl 2>/dev/null || true"],
        )
    except subprocess.CalledProcessError:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def decision_line_counts(container: str) -> dict[str, int]:
    """Snapshot line count for every decision-log file in the container."""
    files = list_decision_files(container)
    counts: dict[str, int] = {}
    for path in files:
        try:
            out = docker_exec_capture(container, ["wc", "-l", path])
            counts[path] = int(out.strip().split()[0])
        except subprocess.CalledProcessError:
            counts[path] = 0
    return counts


def decision_slice_since(
    container: str,
    baselines: dict[str, int],
    dest: Path,
) -> int:
    """Copy new decision-log lines since `baselines` into `dest` (one merged file).

    Returns the number of lines written.
    """
    files_now = list_decision_files(container)
    total = 0
    with dest.open("w", encoding="utf-8") as fh:
        for path in files_now:
            start = baselines.get(path, 0) + 1
            try:
                out = docker_exec_capture(
                    container, ["sh", "-c", f"tail -n +{start} {path} || true"]
                )
            except subprocess.CalledProcessError:
                continue
            for line in out.splitlines():
                if line.strip():
                    fh.write(line + "\n")
                    total += 1
    return total


# ---------------------------------------------------------------------------
# Sim speed (optional)
# ---------------------------------------------------------------------------


class SpeedError(RuntimeError):
    """set_speed did not go through — user asked for >1x, we could not deliver."""


def _raw_ws_send_text(host: str, port: int, payload: str, timeout: float = 5.0) -> None:
    """One-shot WebSocket client: handshake → send one masked text frame → close.

    Zero external dependencies (stdlib only). Fire-and-forget — does not read
    the server's response beyond the handshake. Sufficient for `set_speed`
    which is a one-way command.
    """
    import base64
    import hashlib
    import os as _os
    import socket

    key = base64.b64encode(_os.urandom(16)).decode("ascii")
    handshake = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode("ascii")

    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.sendall(handshake)
        # Read handshake response until \r\n\r\n.
        buf = b""
        deadline = time.monotonic() + timeout
        while b"\r\n\r\n" not in buf:
            if time.monotonic() > deadline:
                raise TimeoutError("WS handshake response timeout")
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("WS handshake: server closed connection")
            buf += chunk

        header_line = buf.split(b"\r\n", 1)[0].decode("ascii", "replace")
        if "101" not in header_line:
            raise ConnectionError(f"WS handshake failed: {header_line!r}")

        # Verify Sec-WebSocket-Accept.
        magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        expected = base64.b64encode(
            hashlib.sha1((key + magic).encode("ascii")).digest()
        ).decode("ascii")
        headers = buf.split(b"\r\n\r\n", 1)[0].decode("ascii", "replace")
        if expected not in headers:
            raise ConnectionError("WS handshake: bad Sec-WebSocket-Accept")

        # Build one masked text frame (FIN=1, opcode=text=0x1).
        data = payload.encode("utf-8")
        mask = _os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < (1 << 16):
            header.append(0x80 | 126)
            header += n.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += n.to_bytes(8, "big")
        header += mask

        sock.sendall(bytes(header) + masked)
        # Do not wait for reply; the server acts on the command immediately.
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass


def set_speed(container: str, factor: float) -> str:
    """Send set_speed via WS. Returns status string on success, raises SpeedError
    on failure.

    Implementation: stdlib raw-socket WS client (no external deps).
    """
    if abs(factor - 1.0) < 1e-6:
        return "speed=1.0x (no WS call needed)"

    port = docker_port(container, PHYS_PORT_INSIDE)  # raises if not running
    payload = json.dumps(
        {
            "type": "command",
            "command": "set_speed",
            "params": {"speed_factor": factor},
        }
    )
    try:
        _raw_ws_send_text("127.0.0.1", int(port), payload, timeout=5.0)
    except Exception as e:  # noqa: BLE001
        raise SpeedError(f"could not set speed={factor}x via WS 127.0.0.1:{port} — {e}") from e
    return f"speed set to {factor}x (compute cap ~1.7-1.8x actual)"


def _which(cmd: str) -> bool:
    from shutil import which

    return which(cmd) is not None


def _ensure_speed(container: str, factor: float, allow_slow: bool) -> str:
    """Wrap set_speed for main(): pretty-format success, exit if user requires >1x and it failed."""
    try:
        return set_speed(container, factor)
    except SpeedError as e:
        if allow_slow:
            return f"WARNING running at 1x — {e}"
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(3)


# ---------------------------------------------------------------------------
# Match driver
# ---------------------------------------------------------------------------


@dataclass
class MatchResult:
    run_id: int
    tag: str
    started_at: str
    ended_at: str
    wall_duration_s: float
    home_name: str
    away_name: str
    home_score: int
    away_score: int
    finish_reason: str
    goals: list[dict[str, Any]] = field(default_factory=list)
    penalties: list[dict[str, Any]] = field(default_factory=list)
    stuck_count: int = 0
    dropped_ball_count: int = 0
    ball_touch_home: int = 0
    ball_touch_away: int = 0
    raw_state_transitions: list[str] = field(default_factory=list)

    def outcome(self, our_side: str) -> str:
        """W/D/L from our_side ∈ {'home','away'}."""
        us = self.home_score if our_side == "home" else self.away_score
        them = self.away_score if our_side == "home" else self.home_score
        if us > them: return "W"
        if us < them: return "L"
        return "D"


def wait_state(gc_port: str, wanted: set[str], timeout_s: float) -> str:
    """Poll /status until game.state ∈ wanted. Returns the state reached."""
    deadline = time.monotonic() + timeout_s
    last_state = ""
    while time.monotonic() < deadline:
        try:
            status = http_get_json(f"http://localhost:{gc_port}/status")
            state = status.get("game", {}).get("state", "")
            if state != last_state:
                print(f"    · state={state}")
                last_state = state
            if state in wanted:
                return state
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            print(f"    · status err: {e}")
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"waited {timeout_s}s for state ∈ {wanted}, last={last_state!r}")


def run_one_match(
    run_id: int,
    tag: str,
    gc_port: str,
    container: str,
    out_dir: Path,
) -> MatchResult:
    started = datetime.now(timezone.utc)
    wall_t0 = time.monotonic()

    # 1. Snapshot events.jsonl and decision-log line counts BEFORE starting.
    events_before = events_line_count(container)
    decisions_before = decision_line_counts(container)

    # 2. Post match/start; expect ok=true, state→ready→playing.
    start_resp = http_post_json(f"http://localhost:{gc_port}/match/start")
    if not start_resp.get("ok", False):
        raise RuntimeError(f"match/start failed: {start_resp!r}")

    # 3. Wait for state=playing (should be quick), then state=finished (up to 1h wall).
    wait_state(gc_port, {"playing"}, timeout_s=60)
    wait_state(gc_port, {"finished"}, timeout_s=MAX_MATCH_WALL_S)

    ended = datetime.now(timezone.utc)
    wall = time.monotonic() - wall_t0

    # 4. Read final status.
    final = http_get_json(f"http://localhost:{gc_port}/status")
    g = final.get("game", {})
    teams = g.get("teams", {})
    home = teams.get("home", {})
    away = teams.get("away", {})

    # 5. Slice events.jsonl since our snapshot.
    events_after = events_line_count(container)
    events = events_slice(container, events_before, events_after)

    # 5b. Slice decision-log jsonl since our snapshot, save alongside run_XXX.json.
    dec_path = out_dir / f"run_{run_id:03d}_decisions.jsonl"
    dec_lines = decision_slice_since(container, decisions_before, dec_path)
    if dec_lines == 0:
        # Nothing was written — remove the empty file so the output tree is
        # honest about whether decisions were captured this match.
        try:
            dec_path.unlink()
        except FileNotFoundError:
            pass

    result = MatchResult(
        run_id=run_id,
        tag=tag,
        started_at=started.isoformat(),
        ended_at=ended.isoformat(),
        wall_duration_s=wall,
        home_name=home.get("name", "home"),
        away_name=away.get("name", "away"),
        home_score=int(home.get("score", 0)),
        away_score=int(away.get("score", 0)),
        finish_reason=g.get("finishReason", ""),
    )

    for ev in events:
        etype = ev.get("type", "")
        timing = ev.get("timing", {})
        t_remaining = timing.get("timeRemaining")
        t_elapsed = None
        if isinstance(t_remaining, (int, float)):
            # match duration is 600s per typical convention; fall back to derived elapsed
            t_elapsed = round(600.0 - float(t_remaining), 1)
        outcome = ev.get("outcome", {}) or {}

        if etype == "goal":
            score = ev.get("score", {})
            side = ev.get("side") or outcome.get("side") or ""
            result.goals.append(
                {
                    "t": t_elapsed,
                    "side": side,
                    "score_after": {
                        "home": score.get("home"),
                        "away": score.get("away"),
                    },
                    "reason": outcome.get("reason", ""),
                }
            )
        elif etype == "penalty":
            result.penalties.append(
                {
                    "t": t_elapsed,
                    "reason": outcome.get("reason", ""),
                    "player": ev.get("player") or outcome.get("player", ""),
                    "side": ev.get("side", ""),
                }
            )
        elif etype == "global_game_stuck":
            result.stuck_count += 1
        elif etype == "dropped_ball":
            result.dropped_ball_count += 1
        elif etype == "ball_touch":
            side = ev.get("side") or ""
            if side == "home":
                result.ball_touch_home += 1
            elif side == "away":
                result.ball_touch_away += 1
        elif etype == "state_changed":
            new = ev.get("state") or outcome.get("state") or ""
            if new:
                result.raw_state_transitions.append(new)

    return result


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def summarize(results: list[MatchResult], our_side: str) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {"n": 0}

    outcomes = [r.outcome(our_side) for r in results]
    goals_for = [
        r.home_score if our_side == "home" else r.away_score for r in results
    ]
    goals_against = [
        r.away_score if our_side == "home" else r.home_score for r in results
    ]
    touches_us = [
        r.ball_touch_home if our_side == "home" else r.ball_touch_away
        for r in results
    ]
    touches_them = [
        r.ball_touch_away if our_side == "home" else r.ball_touch_home
        for r in results
    ]
    penalties_us = []
    for r in results:
        penalties_us.append(sum(1 for p in r.penalties if p.get("side") == our_side))
    stuck = [r.stuck_count for r in results]

    def mean_std(xs: list[int]) -> tuple[float, float]:
        if not xs: return 0.0, 0.0
        m = statistics.mean(xs)
        s = statistics.pstdev(xs) if len(xs) > 1 else 0.0
        return m, s

    gf_m, gf_s = mean_std(goals_for)
    ga_m, ga_s = mean_std(goals_against)
    tu_m, tu_s = mean_std(touches_us)
    tt_m, tt_s = mean_std(touches_them)
    pu_m, _ = mean_std(penalties_us)
    st_m, _ = mean_std(stuck)

    w = outcomes.count("W")
    d = outcomes.count("D")
    l = outcomes.count("L")

    penalty_reasons: dict[str, int] = {}
    for r in results:
        for p in r.penalties:
            if p.get("side") == our_side:
                reason = p.get("reason") or "unknown"
                penalty_reasons[reason] = penalty_reasons.get(reason, 0) + 1

    return {
        "n": n,
        "our_side": our_side,
        "record": {"W": w, "D": d, "L": l},
        "win_rate": w / n,
        "point_rate": (3 * w + d) / (3 * n),  # 3-1-0 points, normalized
        "goals_for": {"mean": gf_m, "std": gf_s, "total": sum(goals_for)},
        "goals_against": {"mean": ga_m, "std": ga_s, "total": sum(goals_against)},
        "net_goals": sum(goals_for) - sum(goals_against),
        "touches_us": {"mean": tu_m, "std": tu_s, "total": sum(touches_us)},
        "touches_them": {"mean": tt_m, "std": tt_s, "total": sum(touches_them)},
        "touch_ratio": (sum(touches_us) / sum(touches_them)) if sum(touches_them) else math.inf,
        "penalties_us": {"mean": pu_m, "total": sum(penalties_us), "by_reason": penalty_reasons},
        "stuck": {"mean": st_m, "total": sum(stuck)},
    }


def render_summary_text(summary: dict[str, Any], tag: str) -> str:
    if summary["n"] == 0:
        return f"=== {tag} (no runs) ==="
    lines = [
        f"=== {tag} ({summary['n']} runs, we are '{summary['our_side']}') ===",
        f"Record:        {summary['record']['W']}W {summary['record']['D']}D {summary['record']['L']}L"
        f"  (win {summary['win_rate']:.0%}, points {summary['point_rate']:.0%})",
        f"Goals for:     {summary['goals_for']['mean']:.2f} ± {summary['goals_for']['std']:.2f}"
        f"  (total {summary['goals_for']['total']})",
        f"Goals against: {summary['goals_against']['mean']:.2f} ± {summary['goals_against']['std']:.2f}"
        f"  (total {summary['goals_against']['total']})",
        f"Net goals:     {summary['net_goals']:+d}",
        f"Touches us/them: {summary['touches_us']['mean']:.0f} vs "
        f"{summary['touches_them']['mean']:.0f}  (ratio {summary['touch_ratio']:.2f})",
        f"Penalties us:  {summary['penalties_us']['mean']:.2f}/game"
        f"  (total {summary['penalties_us']['total']})",
    ]
    if summary["penalties_us"]["by_reason"]:
        reasons = ", ".join(
            f"{k}×{v}"
            for k, v in sorted(
                summary["penalties_us"]["by_reason"].items(),
                key=lambda kv: -kv[1],
            )
        )
        lines.append(f"    reasons: {reasons}")
    lines.append(
        f"Stuck:         {summary['stuck']['mean']:.2f}/game"
        f"  (total {summary['stuck']['total']})"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--runs", type=int, default=10, help="number of matches to run")
    p.add_argument("--tag", type=str, required=True,
                   help="version/tag label; used as output subdir name")
    p.add_argument("--speed", type=float, default=3.0,
                   help="sim speed factor (WS set_speed); actual capped by CPU. "
                        "Batch runs default to 3.0x. Script aborts if speed cannot "
                        "be set — override with --allow-slow.")
    p.add_argument("--allow-slow", action="store_true",
                   help="continue at 1x if WS set_speed is unavailable. "
                        "DO NOT use this for real A/B batches — 10 matches at 1x is 100 min.")
    p.add_argument("--our-side", choices=("home", "away"), default="home",
                   help="which GC side is the agent under test")
    p.add_argument("--output-root", type=str, default="logs/batch",
                   help="output directory root")
    p.add_argument("--container", type=str, default=CONTAINER)
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # Verify container up before we do anything.
    try:
        gc_port = docker_port(args.container, GC_PORT_INSIDE)
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        print(
            "Hint: start the container and deploy the agent via Booster Studio "
            "before running this script.",
            file=sys.stderr,
        )
        return 2

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.output_root) / args.tag / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[batch] tag={args.tag} runs={args.runs} out={out_dir}")
    print(f"[batch] gc_port={gc_port}  container={args.container}")

    speed_msg = _ensure_speed(args.container, args.speed, args.allow_slow)
    print(f"[batch] {speed_msg}")

    results: list[MatchResult] = []
    for i in range(1, args.runs + 1):
        # Re-assert speed before every match. Memory says it persists across
        # matches, but this is cheap insurance against any per-match reset.
        try:
            set_speed(args.container, args.speed)
        except SpeedError as e:
            if not args.allow_slow:
                print(f"[run {i}] speed re-assert failed: {e}", file=sys.stderr)
                break
        print(f"\n[run {i}/{args.runs}] starting…")
        try:
            r = run_one_match(i, args.tag, gc_port, args.container, out_dir)
        except Exception as e:
            print(f"[run {i}] ABORTED: {e}", file=sys.stderr)
            (out_dir / f"run_{i:03d}.error.txt").write_text(f"{type(e).__name__}: {e}\n")
            break

        results.append(r)
        result_path = out_dir / f"run_{i:03d}.json"
        result_path.write_text(json.dumps(r.__dict__, indent=2))
        print(
            f"[run {i}] {r.home_name} {r.home_score}:{r.away_score} {r.away_name}"
            f"  finish={r.finish_reason}  wall={r.wall_duration_s:.0f}s"
            f"  penalties={len(r.penalties)}  stuck={r.stuck_count}"
        )

    summary = summarize(results, args.our_side)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    text = render_summary_text(summary, args.tag)
    (out_dir / "summary.txt").write_text(text + "\n")
    print()
    print(text)
    print(f"\n[batch] all output → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
