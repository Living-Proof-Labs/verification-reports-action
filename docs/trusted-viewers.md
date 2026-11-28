# Trusted inline viewers

The optional `trusted-viewers` input to `./site` names a caller-owned JSON registry.
Each entry pins local viewer HTML by SHA-256, released source tag and commit, and
an exact advisory evaluator identity (id, version, publisher, kind), plus the
recording format accepted by that viewer. Evaluators
provide JSON data; they cannot select or supply executable viewer code.

For each newly produced report the site selects one unambiguous advisory trial
and one report result whose evidence includes that observation digest. It derives
the run, configuration, trial and details checksum from those records. Missing
contributions remain absent. Ambiguous selections, identity mismatches, modified
JSON and invalid paths refuse the build. There is no sample run or evidence pin.
The report's Recorded data link stays available and grading is unchanged.
Before installation or restoration, recording bytes must declare that format
and contain exactly one trial with the selected run, configuration, index and
observation digest. A matching file hash alone is insufficient.

The iframe is inserted into the existing card after ordinary HTML sanitation.
Incoming arbitrary iframes remain rejected. The trusted viewer has scripts and
same-origin access for adjacent digest-verified JSON, so its code must be reviewed;
this is not a sandbox for untrusted executable content. It receives no permission
for top navigation, forms or popups. The released Health loader bounds its fetch,
rejects redirects, verifies bytes before parsing, and validates trial identity.

On restoration, every incoming run file and its complete checksum manifest are
verified before any generated embed is stripped or any checksum is rewritten.
The current caller pin must still validate the viewer. Normal site rendering then
runs and reinstalls the embed. Only newly produced runs and previously decorated
runs are eligible; old unembedded history is never newly decorated. Each eligible
run passes its complete current file manifest before installation as well. With no caller declaration, ordinary reports are
unchanged; an existing embedded viewer without its declaration is refused.

This repository's example caller opts into the Health viewer through
`config/viewers/evaluators.json` in normal G1 verification and documentation
publishing. The HTML asset is unchanged generated Apache-2.0 output of Health
v1.2.0, commit `1c21eeb5d3aafdf00e8aa13c55e544a56fe5ab14`, SHA-256
`9c08b355bd6a90f80a79f1d4369282050473db5ae092ae7ddb41ecf46f490995`.
It is source-generated reviewed code, not a claimed standalone release asset.

This change is generic Action presentation wiring. It adds no evaluator metric,
posture criterion, grading, Reporter API change, special retained-history input,
or separate simulation dispatch. Existing required G1 PR CI exercises the normal
site entry point; ordinary releases and Pages publish the result after protected
delivery. Public embedded-player availability must be checked after deployment.
