"""Repaired, standard-library client for the course's binary TCP protocol."""

import socket


class EndException(Exception):
    """The game ended; the connection remains open."""


class ByeException(Exception):
    """The server ended the session."""


class UnknownCommand(ValueError):
    pass


def recv_exact(sock, length, deadline=None):
    """Read a frame field even when TCP fragments it; EOF is never retried."""
    import time

    data = bytearray()
    while len(data) < length:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Packet deadline exceeded")
            sock.settimeout(remaining)
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise EOFError("Peer closed the connection")
        data.extend(chunk)
    return bytes(data)


class ClientSocket:
    def __init__(self, ip="127.0.0.1", port=5555, connect_timeout=10):
        self._socket = socket.create_connection((ip, port), connect_timeout)
        self._socket.settimeout(None)
        self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self):
        self._socket.close()

    def get_message(self):
        tag = recv_exact(self._socket, 3)
        if tag == b"END":
            raise EndException()
        if tag == b"BYE":
            raise ByeException()
        if tag == b"SET":
            return ["set", list(recv_exact(self._socket, 2))]
        if tag == b"HME":
            return ["hme", list(recv_exact(self._socket, 2))]
        if tag in (b"HUM", b"MAP", b"UPD"):
            count = recv_exact(self._socket, 1)[0]
            width = 2 if tag == b"HUM" else 5
            payload = recv_exact(self._socket, width * count)
            records = [tuple(payload[i:i + width])
                       for i in range(0, len(payload), width)]
            return [tag.decode("ascii").lower(), records]
        raise UnknownCommand(f"Unknown server tag: {tag!r}")

    def send_nme(self, name):
        encoded = name.encode("ascii")
        if not 1 <= len(encoded) <= 255:
            raise ValueError("AI name must contain 1-255 ASCII bytes")
        self._socket.sendall(b"NME" + bytes([len(encoded)]) + encoded)

    def send_mov(self, nb_moves, moves):
        if nb_moves != len(moves) or not 1 <= nb_moves <= 255:
            raise ValueError("MOV count must match 1-255 records")
        payload = bytearray([nb_moves])
        for move in moves:
            if len(move) != 5:
                raise ValueError("Move must be (sx, sy, count, tx, ty)")
            if any(type(value) is not int or not 0 <= value <= 255
                   for value in move) or move[2] == 0:
                raise ValueError("Invalid unsigned-byte move fields")
            payload.extend(move)
        self._socket.sendall(b"MOV" + payload)
