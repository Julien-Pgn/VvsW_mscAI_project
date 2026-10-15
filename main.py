"""Run the protocol client using human-supplied actions only."""
import argparse
import json
import math
import os
import queue
import sys
import threading
import time
from pathlib import Path

from client import ByeException, ClientSocket, EndException
from game_state import GameState


def manual_action(timeout):
    """Wait for one JSON action without blocking past the turn deadline."""
    result = queue.Queue()
    descriptor = sys.stdin.fileno()
    encoding = sys.stdin.encoding or "utf-8"
    print("Moves [[sx,sy,count,tx,ty], ...]: ", end="", flush=True)

    def read():
        # Raw reads hold no buffered-stdin lock during interpreter shutdown
        # if the deadline expires while this daemon is waiting for input.
        try:
            line = bytearray()
            while True:
                char = os.read(descriptor, 1)
                if not char or char == b"\n":
                    result.put(line.decode(encoding) if line else None)
                    return
                line.extend(char)
        except (OSError, UnicodeError) as exc:
            result.put(exc)

    threading.Thread(target=read, daemon=True).start()
    try:
        line = result.get(timeout=max(0, timeout))
    except queue.Empty as exc:
        raise TimeoutError("No manual action received before the deadline") from exc
    if line is None:
        raise EOFError("Action input closed")
    if isinstance(line, Exception):
        raise line
    return json.loads(line)


def play_game(args, actions=None):
    state = GameState()
    client = ClientSocket(args.ip, args.port)
    try:
        client.send_nme(args.name)
        while True:
            try:
                command, payload = client.get_message()
            except EndException:
                state.reset()
                print("Game ended; waiting for new setup on the same connection.", flush=True)
                continue
            except ByeException:
                print("Session ended.", flush=True)
                return
            received = time.monotonic()
            state.apply(command, payload)
            if command not in ("map", "upd"):
                continue
            print(json.dumps({"event": command, **state.snapshot()}), flush=True)
            if command != "upd":
                continue
            # Future strategy code belongs here. No action is chosen automatically.
            deadline = received + args.turn_timeout - 0.1
            if actions is None:
                moves = manual_action(deadline - time.monotonic())
            else:
                try:
                    moves = next(actions)
                except StopIteration as exc:
                    raise ValueError("Actions file exhausted; no move sent") from exc
            state.validate_moves(moves)
            client.send_mov(len(moves), moves, timeout=deadline - time.monotonic())
            print(json.dumps({"event": "mov", "moves": moves}), flush=True)
    finally:
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="ManualClient")
    parser.add_argument("--actions", type=Path,
                        help="JSON array of actions, one list of moves per UPD")
    parser.add_argument("--turn-timeout", type=float, default=2,
                        help="Server turn limit in seconds (default: 2)")
    parser.add_argument("ip", help="Server hostname/IP")
    parser.add_argument("port", type=int, help="Server TCP port")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be in 1..65535")
    if not math.isfinite(args.turn_timeout) or args.turn_timeout <= 0.1:
        parser.error("--turn-timeout must be finite and greater than 0.1")
    try:
        name = args.name.encode("ascii")
        if not 1 <= len(name) <= 255:
            raise ValueError("Name must contain 1-255 ASCII bytes")
        actions = None
        if args.actions is not None:
            data = json.loads(args.actions.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("Actions file must contain an array of turn actions")
            actions = iter(data)
        play_game(args, actions)
    except (OSError, EOFError, ValueError) as exc:
        print(f"Client error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Client stopped.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
