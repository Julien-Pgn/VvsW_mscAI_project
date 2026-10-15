import contextlib
import io
import queue
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from client import ClientSocket, UnknownCommand, recv_exact
from game_state import GameState
from main import manual_action, play_game


class ScriptedSocket:
    """In-memory TCP stream that deliberately fragments every field."""
    def __init__(self, incoming=b""):
        self.incoming = incoming
        self.sent = []
        self.timeout = None
        self.closed = False

    def recv(self, length):
        chunk = self.incoming[:min(length, 1)]
        self.incoming = self.incoming[len(chunk):]
        return chunk

    def sendall(self, data):
        self.sent.append(bytes(data))

    def settimeout(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def setsockopt(self, *args):
        pass

    def close(self):
        self.closed = True


def setup(species=1):
    return (b"SET\x03\x04HUM\x01\x02\x02HME\x00\x00MAP\x02"
            + bytes([0, 0, 0, 10 if species == 1 else 0,
                     10 if species == 2 else 0, 2, 2, 4, 0, 0]))


def board():
    state = GameState()
    state.apply("set", [3, 4])
    state.apply("hme", [0, 0])
    state.apply("map", [(0, 0, 0, 10, 0), (2, 2, 4, 0, 0)])
    return state


class ProtocolTests(unittest.TestCase):
    def test_fragmented_setup_and_zero_update(self):
        sock = ScriptedSocket(setup() + b"UPD\x00")
        with patch("client.socket.create_connection", return_value=sock):
            client = ClientSocket()
        messages = [client.get_message() for _ in range(5)]
        self.assertEqual(messages[0], ["set", [3, 4]])
        self.assertEqual(messages[1], ["hum", [(2, 2)]])
        self.assertEqual(messages[-1], ["upd", []])
        self.assertEqual(messages[3][1][0], (0, 0, 0, 10, 0))

    def test_truncated_packet_raises_eof(self):
        with self.assertRaises(EOFError):
            recv_exact(ScriptedSocket(b"SE"), 3)

    def test_unknown_command(self):
        with patch("client.socket.create_connection", return_value=ScriptedSocket(b"BAD")):
            client = ClientSocket()
        with self.assertRaises(UnknownCommand):
            client.get_message()

    def test_exact_outgoing_bytes_and_timeout_restore(self):
        sock = ScriptedSocket()
        with patch("client.socket.create_connection", return_value=sock):
            client = ClientSocket()
        client.send_nme("Paul")
        client.send_mov(1, [[0, 0, 10, 1, 0]], timeout=1)
        self.assertEqual(sock.sent, [b"NME\x04Paul", b"MOV\x01\x00\x00\x0a\x01\x00"])
        self.assertIsNone(sock.timeout)
        with self.assertRaises(TimeoutError):
            client.send_mov(1, [[0, 0, 10, 1, 0]], timeout=0)
        with self.assertRaises(ValueError):
            client.send_mov(2, [[0, 0, 10, 1, 0]])
        with self.assertRaises(UnicodeEncodeError):
            client.send_nme("André")

    def test_send_failure_restores_timeout(self):
        sock = ScriptedSocket()
        with patch("client.socket.create_connection", return_value=sock):
            client = ClientSocket()
        with patch.object(sock, "sendall", side_effect=socket.timeout):
            with self.assertRaises(socket.timeout):
                client.send_mov(1, [[0, 0, 10, 1, 0]], timeout=1)
        self.assertIsNone(sock.timeout)


class StateTests(unittest.TestCase):
    def test_delta_replaces_and_clears_without_erasing_others(self):
        state = board()
        state.apply("upd", [(0, 0, 0, 0, 0), (1, 0, 0, 10, 0)])
        self.assertNotIn((0, 0), state.cells)
        self.assertEqual(state.cells[1, 0], (0, 10, 0))
        self.assertEqual(state.cells[2, 2], (4, 0, 0))

    def test_invalid_joint_moves(self):
        state = board()
        invalid = [[], [[0, 0, 0, 1, 0]], [[0, 0, 11, 1, 0]],
                   [[0, 0, 1, 2, 0]], [[2, 2, 1, 2, 1]],
                   [[0, 0, 6, 1, 0], [0, 0, 5, 0, 1]],
                   [[0, 0, True, 1, 0]], [[0, 0, 1, -1, 0]]]
        for moves in invalid:
            with self.subTest(moves=moves), self.assertRaises(ValueError):
                state.validate_moves(moves)
        state.cells[1, 0] = (0, 4, 0)
        with self.assertRaises(ValueError):
            state.validate_moves([[0, 0, 1, 1, 0], [1, 0, 1, 2, 0]])
        state.validate_moves([[0, 0, 4, 1, 0]])

    def test_update_before_map(self):
        with self.assertRaises(ValueError):
            GameState().apply("upd", [])


class SessionTests(unittest.TestCase):
    args = SimpleNamespace(ip="localhost", port=5555, name="Test", turn_timeout=2)

    def test_two_games_swap_species_and_bye_on_one_connection(self):
        sock = ScriptedSocket(setup(1) + b"UPD\x00END" + setup(2) + b"UPD\x00ENDBYE")
        with patch("client.socket.create_connection", return_value=sock) as connect:
            with contextlib.redirect_stdout(io.StringIO()):
                play_game(self.args, iter([[[0, 0, 10, 1, 0]], [[0, 0, 10, 0, 1]]]))
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(len(sock.sent), 3)
        self.assertEqual(sock.sent[2], b"MOV\x01\x00\x00\x0a\x00\x01")
        self.assertTrue(sock.closed)

    def test_no_automatic_moves_when_actions_exhausted(self):
        sock = ScriptedSocket(setup() + b"UPD\x00")
        with patch("client.socket.create_connection", return_value=sock):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "exhausted"):
                    play_game(self.args, iter([]))
        self.assertEqual(sock.sent, [b"NME\x04Test"])
        self.assertTrue(sock.closed)

    def test_manual_json_and_deadline(self):
        data = [bytes([v]) for v in b"[[0,0,10,1,0]]\n"]
        with patch("main.os.read", side_effect=data):
            self.assertEqual(manual_action(1), [[0, 0, 10, 1, 0]])
        with patch("main.queue.Queue.get", side_effect=queue.Empty):
            with patch("main.os.read", return_value=b""):
                with self.assertRaises(TimeoutError):
                    manual_action(0)

    def test_manual_timeout_does_not_crash_interpreter_with_open_stdin(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "from main import manual_action; manual_action(0.05)"],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(process.wait(timeout=10), 1)
            error = process.stderr.read().decode()
            self.assertIn("TimeoutError", error)
            self.assertNotIn("Fatal Python error", error)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
