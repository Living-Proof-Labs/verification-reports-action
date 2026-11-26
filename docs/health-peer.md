# Health peer integration proof

The staged API is now released as Capability Reporter v3.16.0 at
`c85d4cb79f3551e827580756fad67101030b1118`. Production `./site` installs its official
wheel in an isolated environment with `site/requirements.txt` and hash verification.
The wheel digest is
`bcdb10c64398fbbf8fc049833cc000ce7bf24585702c8388eb5b18ea60876878`.
The lock includes the same immutable Common library used by Reporter packaging.

`src/reports_presentation.py` adapts already-validated Health to the typed
`EvaluatorPresentation` API, not to core evaluator verdict documents. Reporter
renders the optional peer neutrally as Advisory. No Health means no placeholder.
Grade, Scenario and Integrity remain independent.

Collection retains original Reporter JSON before numeric/path rewrites, plus the
producer's Integrity annotation file, with SHA-256 bindings in `reporter_input`
(`livingproof-reporter-input/1`). Site validation checks those bindings and semantic
identity with the collected report. These private replay files never enter the
public site. The original portable reports/evidence remain unchanged. Replay
preserves the producer's Integrity hero annotation and Pinned inputs HTML/links.
Existing final publication policy still applies to the rendered HTML.

Legacy Health archives without replay inputs keep their validated advisory section,
without inventing a peer. A present invalid descriptor fails, rather than falling
back. Restored valid legacy/peer pages remain byte-identical without private inputs;
missing or tampered sidecars fail. Expiry uses the existing original portable page
and removes Health data and presentation.

`task verify` exercises genuine archives through the real site entry point, nine
on/off/refusal variants and restore/expiry. `Health peer presentation` CI adds
390/430/1280 px checks, keyboard and JS/no-JS checks, and exact Pinned inputs parity
against the pre-change page. See `tests/fixtures/health-peer/README.md` for artifact
IDs/hashes. Reproduction:

```sh
uv run pytest tests/test_health_peer.py -v -s
uv run playwright install chromium
HEALTH_BROWSER_PROOF=1 uv run pytest tests/test_health_peer.py -v -s --timeout=180
```

The existing G1 CI harness may run its normal baseline once; Health adds no simulator
invocation. These archived replay tests are not a claim of a new hosted simulation.
No navigation changes, grade changes or deployment are included.
