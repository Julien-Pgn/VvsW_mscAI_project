# Local experimental-method checks

Performed on 2026-10-15 with Python 3.13.2 on macOS (Darwin 24.6.0,
`arm64`; `uname` reported `ARM64_T8103`). The local matches ran from `/tmp`
against loopback only, without package installations or external connections.

## Strategy check

On `Board(3, 4, {(0, 1): (0, 10, 0), (1, 1): (0, 0, 6)})`, each strategy was
called for species 1 with a 0.04-second budget, depth 2, two samples, and a
12-action limit. Both returned the legal move `[(0, 1, 10, 1, 1)]`, which
eliminates the opposing group. Minimax reported depth 2 and 38 nodes; MCTS
reported 1,663 iterations and 452 root visits. Both returned nonzero search
statistics.

## Small-map match

Command:

```bash
python3 /absolute/path/to/Project/initial_test/run_match.py \
  --games 2 --budget 0.015 --max-plies 60 --max-duration 10 --seed 42
```

The runner was invoked with `/tmp` as its working directory and completed in
4.1 seconds under a 35-second outer timeout. `summary.json` reported success,
methods `minimax` vs `mcts`, and process return codes `[0, 0, 0]`. Both games
ended by elimination: minimax won both, with populations V=11/W=0 and V=0/W=2
after the species assignment swapped. The clients made 15 and 14 decisions.
All server/client stderr files were empty. The replay contained all 36 server
events exactly.

## Larger-map match

Command:

```bash
python3 /absolute/path/to/Project/initial_test/run_match.py \
  --map testmap2.xml --games 2 --budget 0.01 --max-plies 40 \
  --max-duration 10 --seed 42
```

This also ran from `/tmp`; it completed in 2.7 seconds under the 35-second
outer timeout. `summary.json` reported success, the same two methods, and
process return codes `[0, 0, 0]`. Both games reached the 40-ply cap as draws
with V=14/W=14. Each client made 40 decisions. All server/client stderr files
were empty. The replay contained all 87 server events exactly.

## Documented default command

Ran `python3 /absolute/path/to/Project/initial_test/run_match.py` with no
arguments from `/tmp`, under a 45-second outer timeout. It completed in under
one second. The runner used its defaults (thetrap map, minimax vs MCTS, two
games, 0.08-second budget, 100-ply cap, and 120-second per-game duration cap).
Both games ended by elimination, with minimax winning both after the species
assignment swapped. Each player made two decisions. Recorded decision latency
was mean 0.0613 s (maximum 0.0624 s) for minimax and mean 0.0805 s (maximum
0.0806 s) for MCTS. The summary reported success, process return codes
`[0, 0, 0]`, and no client errors or forfeits; all server/client stderr files
were empty. The replay contained all 12 server events exactly.

Results are saved in the gitignored `runs/` directory. These checks show that
the methods run and exchange legal moves with this local development referee.
They do not establish official-server or CTI compatibility, full rule coverage,
competitive strength, or browser rendering of the replay.
