"""Native Python development referee for Vampires vs Werewolves TCP clients."""

import argparse
import json
import math
import random
import socket
import sys
import time
from contextlib import ExitStack

from client import recv_exact
from game import DEFAULT_MAP, load_map


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)


def packet(tag, records, width):
    if len(records) > 255 or any(len(record) != width for record in records):
        raise ValueError("Packet cannot be represented by this protocol")
    return tag + bytes([len(records)]) + bytes(v for record in records for v in record)


def send_setup(sock, board, species):
    homes = [(x, y) for x, y, _ in board.groups(0)]
    x, y, _ = board.groups(species)[0]
    message = (b"SET" + bytes([board.rows, board.columns])
               + packet(b"HUM", homes, 2)
               + b"HME" + bytes([x, y])
               + packet(b"MAP", board.records(), 5))
    sock.sendall(message)


def read_name(sock):
    deadline = time.monotonic() + 10
    if recv_exact(sock, 3, deadline) != b"NME":
        raise ValueError("First client command must be NME")
    count = recv_exact(sock, 1, deadline)[0]
    if count == 0:
        raise ValueError("Name cannot be empty")
    return recv_exact(sock, count, deadline).decode("ascii")


def read_moves(sock, deadline):
    if recv_exact(sock, 3, deadline) != b"MOV":
        raise ValueError("Expected MOV")
    count = recv_exact(sock, 1, deadline)[0]
    if not count:
        raise ValueError("At least one movement is required")
    payload = recv_exact(sock, 5 * count, deadline)
    return [tuple(payload[index:index + 5]) for index in range(0, len(payload), 5)]


def changed_records(previous, current):
    changed = {coordinate for coordinate in previous.cells.keys() | current.cells.keys()
               if previous.cells.get(coordinate) != current.cells.get(coordinate)}
    return current.records(changed)


def finish_game(clients):
    for sock in clients:
        try:
            sock.settimeout(1)
            sock.sendall(b"END")
        except OSError:
            pass


def bye(clients):
    for sock in clients:
        try:
            sock.settimeout(1)
            sock.sendall(b"BYE")
        except OSError:
            pass


def play(clients, names, template, args, game_index):
    board = template.copy()
    species = [1, 2] if game_index % 2 == 0 else [2, 1]
    for index, sock in enumerate(clients):
        sock.settimeout(10)
        send_setup(sock, board, species[index])
    previous_views = [board, board]
    active = game_index % 2
    rng = random.Random(args.seed + game_index)
    start = time.monotonic()
    emit("game_start", game=game_index + 1, players=names, species=species,
         rows=board.rows, columns=board.columns, first_player=active,
         board=board.records())
    reason, winner, healthy = "ply_limit", None, True
    for ply in range(args.max_plies):
        if time.monotonic() - start >= args.max_duration:
            reason = "time_limit"
            break
        sock = clients[active]
        try:
            # Start the budget before transmission, not after receiving MOV.
            deadline = min(time.monotonic() + args.turn_seconds,
                           start + args.max_duration)
            sock.settimeout(max(0.001, deadline - time.monotonic()))
            sock.sendall(packet(b"UPD", changed_records(previous_views[active], board), 5))
            previous_views[active] = board
            moves = read_moves(sock, deadline)
            next_board = board.apply(moves, species[active], rng,
                                     args.strict_record_limit)
            if time.monotonic() > deadline:
                raise TimeoutError("Decision exceeded the turn deadline")
            board = next_board
            emit("move", game=game_index + 1, ply=ply + 1, player=active,
                 species=species[active], moves=moves,
                 board=board.records(),
                 vampires=board.total(1), werewolves=board.total(2))
        except (OSError, EOFError, ValueError) as exc:
            if time.monotonic() >= start + args.max_duration:
                reason, healthy = "time_limit", False
            else:
                reason, winner, healthy = "client_forfeit", 1 - active, False
                emit("client_error", player=active, detail=str(exc))
            break
        if not board.total(1) or not board.total(2):
            reason = "elimination"
            break
        active = 1 - active
    if winner is None:
        populations = [board.total(kind) for kind in species]
        if populations[0] != populations[1]:
            winner = 0 if populations[0] > populations[1] else 1
    emit("game_end", game=game_index + 1, reason=reason, winner_player=winner,
         winner_name=names[winner] if winner is not None else None,
         vampires=board.total(1), werewolves=board.total(2))
    finish_game(clients)
    return healthy


def run(args):
    template = load_map(args.map)
    # A full board has no legal movement in the 1x1 case; two homes already
    # preclude it, but explicitly keep validation near the server boundary.
    with ExitStack() as stack:
        listener = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((args.host, args.port))
        listener.listen(2)
        listener.settimeout(args.wait_seconds)
        actual_host, actual_port = listener.getsockname()
        emit("listening", host=actual_host, port=actual_port,
             map=str(args.map), turn_seconds=args.turn_seconds)
        clients, names = [], []
        try:
            for index in range(2):
                sock, address = listener.accept()
                stack.enter_context(sock)
                clients.append(sock)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                names.append(read_name(sock))
                emit("player_connected", player=index, name=names[-1],
                     address=list(address))
            for game_index in range(args.games):
                if not play(clients, names, template, args, game_index):
                    # Do not reuse a stream containing an incomplete or late MOV.
                    break
        finally:
            bye(clients)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="IPv4 bind address")
    parser.add_argument("--port", type=int, default=5555, help="TCP port (0 selects a free port)")
    parser.add_argument("--map", default=str(DEFAULT_MAP), help="XML map path")
    parser.add_argument("--turn-seconds", type=float, default=2)
    parser.add_argument("--max-plies", type=int, default=200)
    parser.add_argument("--max-duration", type=float, default=300)
    parser.add_argument("--games", type=int, default=1,
                        help="Games on the same connection; species swap each game")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--wait-seconds", type=float, default=300,
                        help="Maximum wait for each player connection")
    parser.add_argument("--strict-record-limit", action="store_true",
                        help="Enforce PDF p.6: records <= number of current groups")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be in 0..65535")
    if any(not math.isfinite(value) or value <= 0 for value in
           (args.turn_seconds, args.max_duration, args.wait_seconds)):
        parser.error("Time limits must be finite and positive")
    if args.max_plies < 1 or args.games < 1:
        parser.error("--max-plies and --games must be positive")
    try:
        run(args)
    except KeyboardInterrupt:
        print("Server stopped.", file=sys.stderr)
        return 130
    except (OSError, EOFError, ValueError, KeyError) as exc:
        print(f"Server error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
