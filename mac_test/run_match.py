"""Start a local referee and two AI clients; save scores, logs and board replay."""

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid


BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", choices=("minimax", "mcts"), default="minimax")
    parser.add_argument("--second", choices=("minimax", "mcts"), default="mcts")
    parser.add_argument("--map", default="thetrap.xml", help="Bundled map name or XML path")
    parser.add_argument("--games", type=int, default=2, help="Use an even count to balance species")
    parser.add_argument("--budget", type=float, default=0.08, help="Seconds of search per move")
    parser.add_argument("--max-plies", type=int, default=100)
    parser.add_argument("--max-duration", type=float, default=120, help="Per-game wall time cap")
    parser.add_argument("--seed", type=int, default=42, help="Server combat seed")
    parser.add_argument("--agent-seed", type=int, default=1001, help="Independent policy RNG seed")
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--candidates", type=int, default=12)
    parser.add_argument("--rollout-depth", type=int, default=6)
    parser.add_argument("--exploration", type=float, default=1.4)
    parser.add_argument("--output", type=Path, help="New results directory (must not exist)")
    args = parser.parse_args()
    if not 0 < args.budget < 1.5:
        parser.error("--budget must be strictly between 0 and 1.5 seconds")
    if not math.isfinite(args.max_duration) or args.max_duration <= 0:
        parser.error("--max-duration must be finite and positive")
    if min(args.games, args.max_plies, args.samples, args.candidates, args.rollout_depth) < 1:
        parser.error("Counts must be positive")
    if args.depth < 2 or args.depth % 2:
        parser.error("--depth must be even and at least 2")
    if not math.isfinite(args.exploration) or args.exploration < 0:
        parser.error("--exploration must be finite and nonnegative")
    map_path = Path(args.map).expanduser()
    if not map_path.is_file():
        map_path = BASE / "maps" / args.map
    if not map_path.is_file():
        parser.error(f"Map does not exist: {args.map}")
    map_path = map_path.resolve()
    # Validate before creating output files or subprocesses.
    from game import load_map
    try:
        load_map(map_path)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    output = (args.output or BASE / "runs" /
              (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])).resolve()
    output.mkdir(parents=True, exist_ok=False)
    methods = [args.first, args.second]
    events, processes, handles = [], [], []
    messages = queue.Queue()
    success, failure, returncodes = False, None, []

    def logfile(name):
        handle = (output / name).open("w", encoding="utf-8")
        handles.append(handle)
        return handle

    server_log = logfile("server.jsonl")

    def consume(stream):
        try:
            for line in stream:
                messages.put(line)
        finally:
            messages.put(None)

    def receive(timeout):
        try:
            line = messages.get(timeout=timeout)
        except queue.Empty:
            return "waiting"
        if line is None:
            return None
        server_log.write(line)
        server_log.flush()
        event = json.loads(line)
        events.append(event)
        if event["event"] == "game_end":
            winner = event["winner_player"]
            label = f"player {winner} ({methods[winner]})" if winner is not None else "draw"
            print(f"Game {event['game']}: {label}; "
                  f"V={event['vampires']} W={event['werewolves']} ({event['reason']})", flush=True)
        return event

    def await_event(kind, seconds=10):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            event = receive(min(0.25, max(0.001, until - time.monotonic())))
            if event is None:
                raise RuntimeError(f"Server stopped before {kind}; see server.stderr.log")
            if isinstance(event, dict) and event["event"] == kind:
                return event
        raise TimeoutError(f"Timed out waiting for {kind}")

    reader = None
    try:
        server = subprocess.Popen(
            [sys.executable, "-u", str(BASE / "server.py"), "--host", "127.0.0.1",
             "--port", "0", "--map", str(map_path), "--games", str(args.games),
             "--max-plies", str(args.max_plies), "--max-duration", str(args.max_duration),
             "--seed", str(args.seed), "--wait-seconds", "10", "--strict-record-limit"],
            cwd=BASE, stdout=subprocess.PIPE, stderr=logfile("server.stderr.log"), text=True)
        processes.append(server)
        reader = threading.Thread(target=consume, args=(server.stdout,), daemon=True)
        reader.start()
        port = await_event("listening")["port"]
        print(f"Local match on 127.0.0.1:{port}: {methods[0]} vs {methods[1]}", flush=True)
        if args.games % 2:
            print("Odd game count: species assignments will not be balanced.", flush=True)
        for player, method in enumerate(methods):
            command = [sys.executable, "-u", str(BASE / "main.py"),
                       "--method", method, "--name", f"{method}-{player}",
                       "--budget", str(args.budget), "--seed", str(args.agent_seed + player),
                       "--depth", str(args.depth), "--samples", str(args.samples),
                       "--candidates", str(args.candidates),
                       "--rollout-depth", str(args.rollout_depth),
                       "--exploration", str(args.exploration), "127.0.0.1", str(port)]
            processes.append(subprocess.Popen(
                command, cwd=BASE, stdout=logfile(f"player{player}.log"),
                stderr=logfile(f"player{player}.stderr.log"), text=True))
            # Guarantee player numbering even if process scheduling changes.
            await_event("player_connected")
        deadline = time.monotonic() + args.games * args.max_duration + 15
        while time.monotonic() < deadline:
            if receive(0.25) is None:
                break
        else:
            raise TimeoutError("Overall match timeout")
        for process in processes:
            process.wait(timeout=5)
        returncodes = [process.returncode for process in processes]
        ends = [event for event in events if event["event"] == "game_end"]
        success = (not any(returncodes) and len(ends) == args.games
                   and not any(event["event"] == "client_error" for event in events))
        if not success:
            failure = "Incomplete match, client error or nonzero exit; inspect saved logs."
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
        failure = str(exc)
    except KeyboardInterrupt:
        failure = "Interrupted by user"
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if reader is not None:
            reader.join(timeout=2)
        # Capture remaining events after an early failure as well.
        while not messages.empty():
            receive(0.01)
        for handle in handles:
            handle.close()
    ends = [event for event in events if event["event"] == "game_end"]
    timings = []
    for player in range(2):
        times = []
        path = output / f"player{player}.log"
        if path.exists():
            for line in path.read_text().splitlines():
                if line.startswith("{"):
                    record = json.loads(line)
                    if record.get("event") == "decision":
                        times.append(record["seconds"])
        timings.append({"moves": len(times),
                        "mean_seconds": sum(times) / len(times) if times else None,
                        "max_seconds": max(times) if times else None})
    settings = {key: str(value) if isinstance(value, Path) else value
                for key, value in vars(args).items()}
    summary = {"success": success, "failure": failure, "settings": settings,
               "map_path": str(map_path), "methods": methods,
               "wins_by_player": [sum(e["winner_player"] == p for e in ends) for p in range(2)],
               "draws": sum(e["winner_player"] is None for e in ends),
               "games": ends, "timing_by_player": timings,
               "process_returncodes": returncodes}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (output / "games.csv").open("w", newline="") as handle:
        fields = ["game", "reason", "winner_player", "winner_name", "vampires", "werewolves"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ends)
    template = (BASE / "replay_template.html").read_text()
    data = json.dumps(events).replace("<", "\\u003c")
    (output / "replay.html").write_text(template.replace("/*MATCH_EVENTS*/[]", data))
    print(f"Results: {output}")
    print(f"Wins: {methods[0]}={summary['wins_by_player'][0]}, "
          f"{methods[1]}={summary['wins_by_player'][1]}; draws={summary['draws']}")
    print(f"Open {output / 'replay.html'} to inspect the board.")
    if failure:
        print(f"Match failed: {failure}", file=sys.stderr)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
