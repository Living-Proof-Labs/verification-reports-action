# Agent instructions

<!-- renovate: guidelines -->
[collaboration-guidelines v9.9.10](https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/v9.9.10/AGENTS.md)
applies, with its procedures, to every change in this repository.

Local additions:
- This repository hosts the reusable Living Proof Verification Reports GitHub Action.
- Keep it as a GitHub Action without a standalone CLI.
- Original repository content is Apache-2.0.
- Use `task setup` and `task verify` locally and in continuous integration.
- Release runs from `.github/workflows/release.yml` after Verify succeeds on `main`. The `release` job tags `main`; every versioned follow-up job uses that job's outputs. Pull requests get a unique candidate version in Verify. Do not seal a forecast with `expected-version`, and do not move tags.
- Substantive changes stay in a draft pull request until independent review.
- Do not enable new registry/security permissions or alter organization settings.
