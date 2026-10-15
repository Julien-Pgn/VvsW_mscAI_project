"""Game state, joint-action validation and sampled combat. No AI libraries."""

from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET


EMPTY = (0, 0, 0)
VAMPIRES = 1
WEREWOLVES = 2
DEFAULT_MAP = Path(__file__).resolve().parent / "maps" / "thetrap.xml"


class Board:
    def __init__(self, rows, columns, cells=None):
        if not (1 <= rows <= 255 and 1 <= columns <= 255):
            raise ValueError("Dimensions must be in 1..255")
        self.rows, self.columns = rows, columns
        self.cells = dict(cells or {})

    def copy(self):
        return Board(self.rows, self.columns, self.cells)

    def in_bounds(self, x, y):
        return 0 <= x < self.columns and 0 <= y < self.rows

    def replace(self, records):
        for x, y, h, v, w in records:
            if not self.in_bounds(x, y):
                raise ValueError("Server sent out-of-bounds coordinates")
            if sum(value > 0 for value in (h, v, w)) > 1:
                raise ValueError("Cell contains several species")
            if h or v or w:
                self.cells[x, y] = (h, v, w)
            else:
                self.cells.pop((x, y), None)

    def records(self, coordinates=None):
        positions = self.cells if coordinates is None else coordinates
        return [(x, y, *self.cells.get((x, y), EMPTY))
                for x, y in sorted(positions)]

    def groups(self, species):
        return [(x, y, populations[species])
                for (x, y), populations in sorted(self.cells.items())
                if populations[species]]

    def total(self, species):
        return sum(populations[species] for populations in self.cells.values())

    def validate(self, moves, species, strict_record_limit=False):
        if species not in (VAMPIRES, WEREWOLVES):
            raise ValueError("Unknown player species")
        if not 1 <= len(moves) <= 255:
            raise ValueError("A turn requires 1-255 moves")
        if strict_record_limit and len(moves) > len(self.groups(species)):
            raise ValueError("More move records than current groups")
        sources, targets = set(), set()
        spent = defaultdict(int)
        for move in moves:
            if len(move) != 5:
                raise ValueError("Each move requires five fields")
            sx, sy, count, tx, ty = move
            if any(type(v) is not int or not 0 <= v <= 255 for v in move):
                raise ValueError("Move fields must be unsigned bytes")
            if count < 1:
                raise ValueError("Cannot move zero creatures")
            if not self.in_bounds(sx, sy) or not self.in_bounds(tx, ty):
                raise ValueError("Move outside the board")
            if max(abs(tx - sx), abs(ty - sy)) != 1:
                raise ValueError("Destination must be an adjacent cell")
            available = self.cells.get((sx, sy), EMPTY)[species]
            spent[sx, sy] += count
            if spent[sx, sy] > available:
                raise ValueError("Wrong species or insufficient source population")
            sources.add((sx, sy))
            targets.add((tx, ty))
        if sources & targets:
            raise ValueError("A cell cannot be both source and target this turn")

    def apply(self, moves, species, rng, strict_record_limit=False):
        """Apply validated departures simultaneously, aggregate arrivals, fight."""
        self.validate(moves, species, strict_record_limit)
        after = self.copy()
        departures, arrivals = defaultdict(int), defaultdict(int)
        for sx, sy, count, tx, ty in moves:
            departures[sx, sy] += count
            arrivals[tx, ty] += count
        for coordinate, count in departures.items():
            populations = list(after.cells[coordinate])
            populations[species] -= count
            if any(populations):
                after.cells[coordinate] = tuple(populations)
            else:
                after.cells.pop(coordinate)
        for coordinate, attackers in sorted(arrivals.items()):
            populations = after.cells.get(coordinate, EMPTY)
            enemy = 3 - species
            if populations[0]:
                winner, survivors = combat(attackers, populations[0], True, rng)
                result_species = species if winner else 0
            elif populations[enemy]:
                winner, survivors = combat(attackers, populations[enemy], False, rng)
                result_species = species if winner else enemy
            else:
                survivors = attackers + populations[species]
                result_species = species
            if survivors > 255:
                raise ValueError("Population exceeds one-byte protocol capacity")
            if survivors:
                result = [0, 0, 0]
                result[result_species] = survivors
                after.cells[coordinate] = tuple(result)
            else:
                after.cells.pop(coordinate, None)
        return after


def combat(attackers, defenders, humans, rng):
    if attackers <= 0 or defenders <= 0:
        raise ValueError("Combat requires positive populations")
    if humans and attackers >= defenders:
        return True, attackers + defenders
    if not humans:
        if 2 * attackers >= 3 * defenders:
            return True, attackers
        if 2 * defenders >= 3 * attackers:
            return False, defenders
    probability = (attackers / (2 * defenders) if attackers < defenders
                   else attackers / defenders - 0.5)
    if rng.random() < probability:
        trials = attackers + defenders if humans else attackers
        return True, sum(rng.random() < probability for _ in range(trials))
    return False, sum(rng.random() < 1 - probability for _ in range(defenders))


def load_map(path):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid map XML: {exc}") from exc
    if root.tag != "Map":
        raise ValueError("Expected a Map XML root")
    board = Board(int(root.attrib["Rows"]), int(root.attrib["Columns"]))
    kinds = {"Humans": 0, "Vampires": VAMPIRES, "Werewolves": WEREWOLVES}
    for node in root:
        if node.tag not in kinds:
            raise ValueError(f"Unknown map element: {node.tag}")
        x, y, count = (int(node.attrib[key]) for key in ("X", "Y", "Count"))
        if not board.in_bounds(x, y) or not 1 <= count <= 255:
            raise ValueError("Invalid map coordinate or population")
        if (x, y) in board.cells:
            raise ValueError("Duplicate map cell")
        values = [0, 0, 0]
        values[kinds[node.tag]] = count
        board.cells[x, y] = tuple(values)
    if any(len(board.groups(species)) != 1 for species in (1, 2)):
        raise ValueError("Map must have one initial group per monster species")
    # Bounds occupied cells, updates, and all possible merged populations.
    # Supplied maps meet this development-server restriction.
    if sum(sum(values) for values in board.cells.values()) > 255:
        raise ValueError("Development server supports at most 255 total initial units")
    return board


def distance(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def single_group_actions(board, species):
    """Conservative baseline: one full-group adjacent movement per turn."""
    for x, y, count in board.groups(species):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if (dx or dy) and board.in_bounds(x + dx, y + dy):
                    yield [(x, y, count, x + dx, y + dy)]
