# Genuine Health peer replay

Unmodified authenticated GitHub Actions archives from public G1 run 45760910690:

- `g1-report.zip`: artifact 15089667971, SHA-256
  `dd0f42c25b3ed235ccda95b311edbebc94717fdf240b723058e46fefb4aba71e`.
- `reporter-original.zip`: original same-run Reporter artifact 15090455237,
  SHA-256 `a14d7d6a106d4ef52da5af419d43ae43d712c166eaa7a6ce7b8bc062fa978e8c`.
- `baseline.html`: pre-change Action-generated page from that G1 archive, retained
  to compare Pinned inputs markup/links and observable report behavior.

Trial `walk-hallway-unitree-g1-stock-20261126T052209Z`, stock 1. Health digest
`ef54f3fddeecf66c3678264fd3b5115533a06be2539f2c9648ff3a6742e97038`.
The real sidecar records 574 simulator samples, minimum base height 0.74814 m at
8.46 s, and a valid empty robot telemetry stream. This is advisory simulator
measurement, not a robot health pass. Test expectations use those values; runtime
rendering does not hard-code them. No simulation was rerun to produce these fixtures.

The probes modify temporary copies for negative cases only. GitHub transport for
history restore is replaced with a local Pages archive; extraction, validation,
publication, expiry and the site entry point are real.
