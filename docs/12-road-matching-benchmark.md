# Road-matching correctness benchmark

The first driving-core benchmark is deliberately synthetic, deterministic and
offline. It provides a small safety regression gate before recorded,
hand-reviewed Dutch drives are available. No coordinates originate from a
device and the fixture contains no personal data.

## Scope

[`tests/fixtures/matching_cases.geojson`](../tests/fixtures/matching_cases.geojson)
contains readable road geometry, directed graph metadata and timestamped GPS
fixes for six failure modes:

- separated opposite carriageways;
- a same-direction parallel service road;
- a connected ramp/fork with temporary branch uncertainty;
- a grade-separated crossing without a shared graph node;
- compass and GPS drift while stationary;
- lateral GPS drift while moving.

Every fix declares the allowed status (`accepted`, `ambiguous`, or `unmatched`)
and the segment identities that would be safe to accept. Ambiguous and
unmatched are valid fail-closed outcomes. An accepted wrong direction or wrong
carriageway is a release-blocking failure.

Run the executable baseline with:

```bash
python3 scripts/benchmark_matching.py
python3 scripts/benchmark_matching.py --json
```

The report is split per scenario and records accepted, ambiguous, unmatched,
wrong-road, wrong-direction, wrong-carriageway and switch counts. The default
replay uses a small Python reference ranker to prove the fixture and evaluator
are executable. **It does not execute or claim parity with the browser
matcher.**

## Browser output contract

The evaluator can assess another matcher without changing the fixture:

```bash
python3 scripts/benchmark_matching.py --observations browser-results.json
```

The JSON maps every scenario id to one result per input fix:

```json
{
  "dual_carriageway_opposite": [
    {"status": "accepted", "segment_id": "dual-a4-north", "confidence": 0.91}
  ]
}
```

The production browser core directly replays this same fixture in
`tests/js/road_match_core.test.js`. That test verifies that every accepted
result belongs to the fixture's safe segment set. The Python observation
contract remains useful for comparing other algorithms and archived replay
outputs; neither synthetic replay is a measured national accuracy claim.

## Interpretation

This synthetic set is intentionally small and cannot establish a percentage
accuracy or national coverage. The current Noord-Holland runtime baseline
(2026-07-15) contains 54,829 graph segments and 16,187 evaluated source
bindings: 4,266 accepted, 8,729 ambiguous and 3,192 rejected. Those counts are
operational coverage context only; they are not matcher accuracy metrics.

The next evidence layer is recorded-drive replay with hand-reviewed ground
truth. New cases should be added as fixtures whenever a real-world failure is
found, while retaining the rule that uncertain input may remain unbound.
