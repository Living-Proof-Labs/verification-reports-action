# Site parity for #75

The reports section moved from the action's own Python renderer to the pinned reporter's
`livingproof-capability-report site` command (v3.20.0). These lists show the deployed bytes
did not change.

`tools/site_parity.py` drives `src/site_action.py` the way `site/action.yml` does (inputs in
the environment, restore off, `GITHUB_RUN_ID` set to the run) and writes one `SHA256SUMS`
list per case:

| Case | Input | Caller | Files |
|---|---|---|---|
| `golden` | `tools/site_fixture.py` runs 100 then 200 (retention 7 days) | `Living-Proof-Labs/livingproof-unitree-g1` | 54 |
| `g1-46115314157` | artifact `g1-verification-report` 15252230242 (`sha256:b4c5f3ec…4f66`), Verify run 46115314157 | `Living-Proof-Labs/verification-reports-action` | 25 |
| `g1-46118968220` | artifact 15253136714 (`sha256:c566d0e8…aed4`), Verify run 46118968220 on `main` | same | 25 |
| `g1-46122371783` | artifact 15254318050 (`sha256:51b53f14…787c`), Verify run 46122371783 on `main` | same | 25 |
| `g1-history` | the three runs built in turn into one history | same | 63 |

- `before/`: the action at `main` `2e95df2` (its own renderer, reporter wheel v3.17.0).
- `after/`: this branch (the reporter's `site` command, wheel v3.20.0).

`diff -r before after` is empty, and `before/golden.SHA256SUMS` is the committed
`tests/fixtures/site-golden/SHA256SUMS`. To repeat with the artifacts downloaded:

    python3 tools/site_parity.py OUT --g1 46115314157=DIR --g1 46118968220=DIR --g1 46122371783=DIR

Run it with the python of the locked dev environment (`uv run python …`), which has the
pinned reporter.
