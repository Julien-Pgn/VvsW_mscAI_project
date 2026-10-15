"""Two opponent-aware experimental methods, implemented without AI libraries.

Both share legal action candidates and evaluation to make the search comparison
useful. Neither is a finished tournament model or a claim of optimal play.
"""

import math
import time

from game import distance, single_group_actions


WIN = 10000.0


def terminal(board):
    return not board.total(1) or not board.total(2)


def evaluate(board, species):
    """Antisymmetric material, village access, concentration and tactical safety."""
    enemy = 3 - species
    ours, theirs = board.total(species), board.total(enemy)
    if not ours and not theirs:
        return 0.0
    if not ours:
        return -WIN
    if not theirs:
        return WIN

    def features(side):
        groups, opponents = board.groups(side), board.groups(3 - side)
        value = 12.0 * board.total(side)
        # Credit each village once to its best currently sufficient group.
        for x, y, count in board.groups(0):
            arrivals = [distance((x, y), (gx, gy))
                        for gx, gy, size in groups if size >= count]
            if arrivals:
                value += 5.0 * count / (1 + min(arrivals))
        # Pursue favorable fights; escape immediate deterministic elimination.
        for x, y, count in groups:
            for ex, ey, other in opponents:
                separation = distance((x, y), (ex, ey))
                if 2 * count >= 3 * other:
                    value += 3.0 * other / (1 + separation)
                if separation <= 1 and 2 * other >= 3 * count:
                    value -= 9.0 * count
        value -= 0.4 * max(0, len(groups) - 1)
        return value

    return max(-WIN / 2, min(WIN / 2, features(species) - features(enemy)))


def candidates(board, species, limit=12):
    """Full moves plus threshold partial departures; no same-turn move chains.

    Every action contains one record, so it also respects the strict p.6 record
    limit. A partial departure splits by leaving the remainder at its source.
    Multi-group coordination is intentionally outside this initial comparison.
    """
    enemy = 3 - species
    actions = []
    for action in single_group_actions(board, species):
        actions.append(action)
        sx, sy, available, tx, ty = action[0]
        humans, vampires, wolves = board.cells.get((tx, ty), (0, 0, 0))
        defenders = (humans, vampires, wolves)[enemy]
        threshold = humans or ((3 * defenders + 1) // 2 if defenders else 0)
        if 0 < threshold < available:
            actions.append([(sx, sy, threshold, tx, ty)])

    def rank(action):
        # Cheap deterministic ordering: prioritize guaranteed captures, then
        # distance progress. Never substitute this ordering for rival search.
        sx, sy, count, tx, ty = action[0]
        target = board.cells.get((tx, ty), (0, 0, 0))
        score = 0.0
        if target[0] and count >= target[0]:
            score += 20 * target[0]
        if target[enemy] and 2 * count >= 3 * target[enemy]:
            score += 40 * target[enemy]
        if target[enemy] and 2 * target[enemy] >= 3 * count:
            score -= 50 * count
        for x, y, humans in board.groups(0):
            if count >= humans:
                score += humans * (distance((sx, sy), (x, y))
                                   - distance((tx, ty), (x, y)))
        for x, y, other in board.groups(enemy):
            if distance((tx, ty), (x, y)) <= 1 and 2 * other >= 3 * count:
                score -= 12 * count
        return score

    actions.sort(key=rank, reverse=True)
    return actions[:limit]


class SearchExpired(Exception):
    pass


def sampled_minimax(board, species, budget, rng, *, depth=4, samples=2,
                    limit=12, **unused):
    """Iterative-deepening adversarial search with sampled chance branches.

    MAX on our turns, MIN on theirs, mean over sampled combat outcomes. This is
    approximate expectiminimax, not exhaustive probability enumeration. Finish
    all root candidates at one depth before replacing the previous result.
    """
    deadline = time.monotonic() + budget
    root_actions = candidates(board, species, limit)
    if not root_actions:
        raise ValueError("No legal action")
    best, completed_depth, nodes = root_actions[0], 0, 0

    def check():
        if time.monotonic() >= deadline:
            raise SearchExpired()

    def value(state, side, remaining):
        nonlocal nodes
        check()
        nodes += 1
        if remaining == 0 or terminal(state):
            return evaluate(state, species)
        options = candidates(state, side, limit)
        scores = [action_value(state, side, action, remaining) for action in options]
        return max(scores) if side == species else min(scores)

    def action_value(state, side, action, remaining):
        outcomes = []
        # Deduplicate deterministic/repeated samples without re-evaluating them.
        values = {}
        for _ in range(samples):
            check()
            after = state.apply(action, side, rng)
            key = tuple(sorted(after.cells.items()))
            if key not in values:
                values[key] = value(after, 3 - side, remaining - 1)
            outcomes.append(values[key])
        return sum(outcomes) / len(outcomes)

    for horizon in range(2, depth + 1, 2):
        try:
            scores = [action_value(board, species, action, horizon)
                      for action in root_actions]
        except SearchExpired:
            break
        index = max(range(len(scores)), key=scores.__getitem__)
        best, completed_depth = root_actions[index], horizon
        # The previous winner gets considered early in the next iteration.
        root_actions.insert(0, root_actions.pop(index))
    return best, {"completed_depth": completed_depth, "nodes": nodes}


class Edge:
    def __init__(self, action):
        self.action = action
        self.visits = 0
        self.reward = 0.0
        self.outcomes = {}


class Node:
    def __init__(self, board, side, limit):
        self.board, self.side, self.visits = board, side, 0
        self.edges = [Edge(action) for action in candidates(board, side, limit)]


def mcts(board, species, budget, rng, *, rollout_depth=6, exploration=1.4,
         limit=12, **unused):
    """Adversarial UCT, explicit stochastic outcomes, heuristic short rollouts."""
    deadline = time.monotonic() + budget
    root = Node(board, species, limit)
    if not root.edges:
        raise ValueError("No legal action")
    iterations = 0

    def reward(state):
        score = evaluate(state, species)
        if terminal(state):
            return 1.0 if score > 0 else 0.0 if score < 0 else 0.5
        return 0.5 + 0.49 * math.tanh(score / 100.0)

    def rollout(state, side, remaining):
        for _ in range(remaining):
            if time.monotonic() >= deadline or terminal(state):
                break
            options = candidates(state, side, limit)
            # Mostly tactical/strategic ordering, occasionally exploratory.
            action = options[0] if rng.random() < 0.7 else rng.choice(options)
            state = state.apply(action, side, rng)
            side = 3 - side
        return reward(state)

    while time.monotonic() < deadline:
        node, nodes, edges = root, [root], []
        result = None
        remaining = rollout_depth
        while remaining and not terminal(node.board):
            if time.monotonic() >= deadline:
                break
            unvisited = [edge for edge in node.edges if not edge.visits]
            if unvisited:
                # Stable root ordering ensures useful fallback at tiny budgets.
                edge = unvisited[0]
            else:
                sign = 1 if node.side == species else -1
                edge = max(node.edges, key=lambda e:
                           sign * (e.reward / e.visits)
                           + exploration * math.sqrt(math.log(node.visits + 1) / e.visits))
            state = node.board.apply(edge.action, node.side, rng)
            key = tuple(sorted(state.cells.items()))
            edges.append(edge)
            remaining -= 1
            if key not in edge.outcomes:
                child = Node(state, 3 - node.side, limit)
                edge.outcomes[key] = child
                nodes.append(child)
                result = rollout(state, child.side, remaining)
                break
            node = edge.outcomes[key]
            nodes.append(node)
        if result is None:
            result = reward(nodes[-1].board)
        for visited in nodes:
            visited.visits += 1
        for visited in edges:
            visited.visits += 1
            visited.reward += result
        iterations += 1
    selected = max(root.edges, key=lambda e: (e.visits,
                   e.reward / e.visits if e.visits else -1))
    return selected.action, {"iterations": iterations, "root_visits": selected.visits}


METHODS = {"minimax": sampled_minimax, "mcts": mcts}
