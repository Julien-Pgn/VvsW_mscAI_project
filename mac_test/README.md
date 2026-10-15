# Initial local AI experiment

This directory contains everything needed to run **sampled adversarial search
against Monte Carlo tree search on your Mac**. Python 3.9+ and its standard
library are sufficient; there are no pip packages, Windows applications or
external services to install.

## Quick start

From the `Artificial-Intelligence` project root:

```bash
cd Project/initial_test
python3 run_match.py
```

That single command starts a local Python server on a free loopback port and two
independent AI client processes. It plays two games on `thetrap.xml`, swapping
species so each method plays both. Both algorithms normally use at most about
0.08 seconds of search per move; the referee's move deadline is two seconds.
Default game limits are 100 plies or 120 seconds per game. You do not need three
terminals, a network endpoint or to choose an unused port.

At the end, the terminal prints the winner of each game and a new results folder
under `runs/`. Open that folder's **`replay.html`** in Safari or another browser
to watch the match or scrub through individual plies. This is a standalone
offline replay with embedded data; no web server is required. It shows the
board from the referee logs, not either method's predicted future board.

Use Ctrl-C to stop the runner; it cleans up the child processes. A failed run
returns a nonzero exit status, saves diagnostic logs, and is not reported as a
successful strategy comparison. Server startup failures can be inspected in
`server.stderr.log`.

## The two methods

| Method | How it chooses a move | Useful experiments |
| --- | --- | --- |
| `minimax` | Iterative-deepening adversarial search: maximize our score, minimize over opponent replies, average sampled combat outcomes. This is **approximate expectiminimax**, rather than exact enumeration of chance outcomes. | Change maximum ply depth, number of combat samples, candidate count and time budget. |
| `mcts` | UCT tree search with opponent nodes minimizing our reward, sampled combat outcomes attached to actions, and short heuristic rollouts. The root chooses the most visited action. | Change exploration strength, rollout depth, candidate count and time budget. |

Both are implemented directly in [strategies.py](strategies.py). Neither uses an
AI/search/tree/graph library. The methods share:

- An evaluator combining material, access to villages, force concentration,
  favorable pursuit and immediate elimination threats.
- Full-group adjacent moves and partial departures sized to capture an adjacent
  village or defeat an adjacent enemy deterministically. A partial departure
  leaves the remainder at its source and can therefore create a second group.
- A bounded candidate action set and an already legal fallback if search runs
  out of time. The minimax method retains the last fully completed depth.
- A separate private RNG from the referee's combat RNG; neither AI is told the
  referee's random seed or future choices.

Each candidate moves **one group in one record**. Coordinated same-turn moves
from multiple groups and multi-destination splits are omitted from these initial
methods, although the referee supports them. Both methods therefore reason over
a restricted opponent action set. These are viable experimental baselines, not
finished tournament agents or a claim that either is the best method.

At extremely short budgets, minimax may report `completed_depth: 0`, meaning it
used its ordered legal fallback rather than completing opponent search. Increase
`--budget` or reduce `--depth`, `--samples` or `--candidates` when comparing search
quality. The timing values saved in client logs include move validation and
sending as well as the search. Local timing is not a guarantee on CTI.

## Example experiments

The larger rectangular supplied map:

```bash
python3 run_match.py --map testmap2.xml
```

More games and a different server combat seed:

```bash
python3 run_match.py --games 6 --seed 123 --budget 0.15
```

Smaller minimax tree with more combat sampling:

```bash
python3 run_match.py --depth 2 --samples 4 --candidates 8 --budget 0.15
```

Longer MCTS rollouts and more exploration:

```bash
python3 run_match.py --rollout-depth 10 --exploration 1.8 --budget 0.15
```

Reverse which process starts as vampires, or run a method against itself:

```bash
python3 run_match.py --first mcts --second minimax
python3 run_match.py --first mcts --second mcts
```

Custom XML map and a custom, previously nonexistent results directory:

```bash
python3 run_match.py --map /absolute/path/to/map.xml --output /tmp/my-ai-experiment
```

`python3 run_match.py --help` lists all options. With an even game count each
method receives each species equally. This server starts vampires first, so
swapping species also swaps which method moves first. Per-game seeds advance
from `--seed`; `--agent-seed` separately controls policy sampling. Time-bounded
search can produce different decisions on repeat runs despite fixed seeds, due
to scheduling and the number of iterations completed. Small match sets are
diagnostics, not statistically reliable rankings.

To alter strategy behavior, edit `evaluate`, `candidates`, `sampled_minimax` or
`mcts` in `strategies.py`. Change one thing at a time, and compare multiple maps,
seeds and both species. The current CLI applies the same shared settings to both
players; method-specific flags affect only their respective method.

## Results and replay

Each run saves:

| Output | Contents |
| --- | --- |
| `summary.json` | Arguments, method names, per-game outcomes, win/draw counts, process exit status and mean/maximum client decision times |
| `games.csv` | One row per completed game, suitable for comparison in a spreadsheet |
| `server.jsonl` | Connections, setup, every accepted move and actual board, results or client errors |
| `player0.log`, `player1.log` | Setup and decision records with search work and elapsed seconds |
| `*.stderr.log` | Process errors, normally empty on success |
| `replay.html` | Offline game selector, board, playback and ply slider |

`runs/` and Python caches are gitignored. Logs are kept so results can be
reviewed. `winner_player` identifies connection 0 or 1, while `winner_name`
also identifies the method. A winner of `null` means a draw. If both players use
the same method, compare player indices to distinguish them.

## Contents and provenance

- `client.py`, `game.py`, `server.py`, `main.py`: copies of the separate
  `mac_starterkit` files, adapted **only here**. The board's default map now points
  inside this directory; the server additionally logs board snapshots and the
  client selects between the two new methods.
- `maps/thetrap.xml`, `maps/testmap2.xml`: copies of the XML maps supplied in
  `Softwares-20261012/Map generator`.
- `strategies.py`, `run_match.py`, `replay_template.html`: new experimental
  methods, local runner and offline replay template.
- `VERIFICATION.md`: actual local test results and remaining limitations.

The directory can be copied elsewhere and run without the original folders.
The Windows `.exe`/DLLs and the incomplete official starter are not required for
these local tests and were not duplicated into the runnable environment.
`Softwares-20261012`, `starterkit`, `mac_starterkit` and the master guide are left
unchanged by this setup.

## Referee limitations and official validation

This is the native **development referee**, not the original Windows server.
The final AI must still execute on CTI and communicate with the professor's
actual server. Successful local matches do not prove official compatibility.

- Maps must have one starting group of each species, dimensions in 1..255,
  and at most **255 total initial units including humans**. Both bundled maps
  meet the restriction. Counts and updates must fit the one-byte wire format.
- Combat uses the PDF's deterministic human and 1.5 monster thresholds before
  sampling battles. Arrivals into the same cell aggregate before combat.
- The runner enables the strict PDF p.6 limit on records per group. Both
  experimental methods use one move record per turn and meet this restriction.
- An illegal move, timeout or disconnect forfeits the game and ends the session.
  The PDF instead says a timeout passes play to the opponent. This development
  tool does not validate official timeout handling.
- At time/ply limits, the larger total monster population wins; equal totals
  draw. The professor's precise dominance/tie interpretation is unresolved.
- There is no recurring day/night cycle. `time_limit` means the configured wall
  time expired; it is not a separate simulated game mechanic.
- The runner uses localhost only and performs no remote access or installation.
  The offline replay is not the original Windows or Twilight GUI.

For manual use, launch `server.py` and two `main.py` processes separately.
For example, the client accepts the course's final host/port arguments:

```bash
python3 main.py --method minimax --budget 0.15 SERVER_HOST SERVER_PORT
```

This interface is intended for later official-server testing, but official-server
and CTI compatibility remain unverified here.
