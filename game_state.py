"""Authoritative board updates and action legality; no strategy or simulation."""


class GameState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.rows = self.columns = 0
        self.home = self.species = None
        self.human_homes = []
        self.cells = {}
        self.ready = False

    def in_bounds(self, x, y):
        return 0 <= x < self.columns and 0 <= y < self.rows

    def apply(self, command, payload):
        if command == "set":
            self.reset()
            self.rows, self.columns = payload
            if not self.rows or not self.columns:
                raise ValueError("Board dimensions must be positive")
        elif command == "hum":
            if not self.rows or any(not self.in_bounds(*cell) for cell in payload):
                raise ValueError("Invalid human homes or HUM before SET")
            self.human_homes = [tuple(cell) for cell in payload]
        elif command == "hme":
            self.home = tuple(payload)
            if not self.in_bounds(*self.home):
                raise ValueError("Home outside board or received before SET")
        elif command in ("map", "upd"):
            if not self.rows or (command == "map" and self.home is None):
                raise ValueError("MAP/UPD received before setup")
            if command == "upd" and not self.ready:
                raise ValueError("UPD received before MAP")
            if command == "map":
                self.cells.clear()
            for x, y, humans, vampires, werewolves in payload:
                if not self.in_bounds(x, y):
                    raise ValueError("Cell outside board")
                if sum(v > 0 for v in (humans, vampires, werewolves)) > 1:
                    raise ValueError("Cell contains multiple species")
                if humans or vampires or werewolves:
                    self.cells[x, y] = (humans, vampires, werewolves)
                else:
                    self.cells.pop((x, y), None)
            if command == "map":
                home = self.cells.get(self.home, (0, 0, 0))
                if bool(home[1]) == bool(home[2]):
                    raise ValueError("Home must contain exactly one monster species")
                self.species = 1 if home[1] else 2
                self.ready = True
        else:
            raise ValueError(f"Unexpected state command: {command}")

    def validate_moves(self, moves):
        if not self.ready:
            raise ValueError("Board not initialized")
        if not isinstance(moves, list) or not 1 <= len(moves) <= 255:
            raise ValueError("Supply a list of 1-255 moves")
        departures, destinations = {}, set()
        for move in moves:
            if not isinstance(move, (list, tuple)) or len(move) != 5:
                raise ValueError("Each move must be [sx, sy, count, tx, ty]")
            if any(type(v) is not int or not 0 <= v <= 255 for v in move):
                raise ValueError("Move fields must be integers in 0..255")
            sx, sy, count, tx, ty = move
            if count == 0 or not self.in_bounds(sx, sy) or not self.in_bounds(tx, ty):
                raise ValueError("Positive count and in-bounds coordinates required")
            if max(abs(tx - sx), abs(ty - sy)) != 1:
                raise ValueError("Destination must be an adjacent cell")
            source = (sx, sy)
            departures[source] = departures.get(source, 0) + count
            population = self.cells.get(source, (0, 0, 0))[self.species]
            if departures[source] > population:
                raise ValueError("Source does not contain enough of your creatures")
            destinations.add((tx, ty))
        if destinations.intersection(departures):
            raise ValueError("A cell cannot be both source and destination this turn")

    def snapshot(self):
        return {"rows": self.rows, "columns": self.columns,
                "home": self.home,
                "species": {1: "vampires", 2: "werewolves"}.get(self.species),
                "cells": [[x, y, *population]
                          for (x, y), population in sorted(self.cells.items())]}
