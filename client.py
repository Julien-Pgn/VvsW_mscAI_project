"""Binary TCP transport for the official Vampires vs Werewolves server."""
import socket


class EndException(Exception):
    """Game ended; the connection may receive another setup."""


class ByeException(Exception):
    """Session ended."""


class UnknownCommand(ValueError):
    pass


def recv_exact(sock, length):
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise EOFError("Server closed the connection")
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
            payload = recv_exact(self._socket, count * width)
            return [tag.decode("ascii").lower(),
                    [tuple(payload[i:i + width])
                     for i in range(0, len(payload), width)]]
        raise UnknownCommand(f"Unknown server command: {tag!r}")

    def send_nme(self, name):
        encoded = name.encode("ascii")
        if not 1 <= len(encoded) <= 255:
            raise ValueError("Name must contain 1-255 ASCII bytes")
        self._socket.sendall(b"NME" + bytes([len(encoded)]) + encoded)

    def send_mov(self, nb_moves, moves, timeout=None):
        if nb_moves != len(moves) or not 1 <= nb_moves <= 255:
            raise ValueError("MOV must contain 1-255 records matching its count")
        payload = bytearray([nb_moves])
        for move in moves:
            if len(move) != 5 or any(type(v) is not int or not 0 <= v <= 255
                                     for v in move) or move[2] == 0:
                raise ValueError("Move must be five bytes: sx, sy, count>0, tx, ty")
            payload.extend(move)
        if timeout is not None and timeout <= 0:
            raise TimeoutError("Turn deadline exceeded before sending")
        previous_timeout = self._socket.gettimeout()
        try:
            self._socket.settimeout(timeout)
            self._socket.sendall(b"MOV" + payload)
        finally:
            self._socket.settimeout(previous_timeout)
