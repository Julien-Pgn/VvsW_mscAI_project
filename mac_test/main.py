"""TCP player with two selectable, opponent-aware experimental AI methods."""

import argparse
import json
import random
import sys
import time

from client import ByeException, ClientSocket, EndException
from game import Board
from strategies import METHODS


def play_game(args):
    client = ClientSocket(args.ip, args.port)
    board, home, species = None, None, None
    rng = random.Random(args.seed)
    try:
        client.send_nme(args.name)
        while True:
            try:
                command, payload = client.get_message()
            except EndException:
                print("Game ended; waiting for another setup.", flush=True)
                board, home, species = None, None, None
                continue
            except ByeException:
                print("Server ended the session.", flush=True)
                return
            if command == "set":
                board = Board(*payload)
                home, species = None, None
            elif command == "hme":
                home = tuple(payload)
            elif command == "hum":
                pass  # Coordinates alone do not contain populations.
            elif command == "map":
                if board is None or home is None:
                    raise ValueError("MAP received before SET/HME")
                board.cells.clear()
                board.replace(payload)
                values = board.cells.get(home, (0, 0, 0))
                if values[1]:
                    species = 1
                elif values[2]:
                    species = 2
                else:
                    raise ValueError("Home cell contains no monster group")
                print(f"Playing {'vampires' if species == 1 else 'werewolves'} "
                      f"on {board.columns}x{board.rows}.", flush=True)
            elif command == "upd":
                if board is None or species is None:
                    raise ValueError("UPD received before board initialization")
                board.replace(payload)
                started = time.monotonic()
                moves, statistics = METHODS[args.method](
                    board, species, args.budget, rng, depth=args.depth,
                    samples=args.samples, limit=args.candidates,
                    rollout_depth=args.rollout_depth, exploration=args.exploration)
                board.validate(moves, species)
                client.send_mov(len(moves), moves)
                print(json.dumps({"event": "decision", "method": args.method,
                                  "seconds": time.monotonic() - started,
                                  "moves": moves, **statistics}), flush=True)
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="ExperimentAI")
    parser.add_argument("--method", choices=METHODS, default="minimax")
    parser.add_argument("--seed", type=int, default=1001)
    parser.add_argument("--depth", type=int, default=4, help="Minimax maximum even ply depth")
    parser.add_argument("--samples", type=int, default=2, help="Combat samples per minimax action")
    parser.add_argument("--candidates", type=int, default=12)
    parser.add_argument("--rollout-depth", type=int, default=6)
    parser.add_argument("--exploration", type=float, default=1.4)
    parser.add_argument("--budget", type=float, default=0.08,
                        help="Search budget in seconds, strictly between 0 and 1.5")
    parser.add_argument("ip", help="Server host/IP (penultimate argument)")
    parser.add_argument("port", type=int, help="Server TCP port (last argument)")
    args = parser.parse_args()
    if args.depth < 2 or args.depth % 2:
        parser.error("--depth must be even and at least 2")
    if min(args.samples, args.candidates, args.rollout_depth) < 1:
        parser.error("Samples, candidates and rollout depth must be positive")
    import math
    if not math.isfinite(args.exploration) or args.exploration < 0:
        parser.error("Exploration must be finite and nonnegative")
    if not 0 < args.budget < 1.5:
        parser.error("--budget must be strictly between 0 and 1.5 seconds")
    if not 1 <= args.port <= 65535:
        parser.error("port must be in 1..65535")
    try:
        args.name.encode("ascii")
        if not 1 <= len(args.name) <= 255:
            parser.error("--name must contain 1-255 ASCII bytes")
        play_game(args)
    except (OSError, EOFError, ValueError) as exc:
        print(f"Client error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
