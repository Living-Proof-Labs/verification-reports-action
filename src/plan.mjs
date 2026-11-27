// SPDX-License-Identifier: Apache-2.0
import { healthEnabled, resolveHealth } from './health.mjs';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { matrixFor, planDefinitions } from './definitions.mjs';
import { readEvaluatorsFile } from './evaluators.mjs';
import { githubProvenanceClient } from './github-provenance.mjs';
import { writeGithubOutput } from './github-output.mjs';
import { assertPinnedInputs, fixtureProvenance, ignoredEvaluatorNotice, resolvedTrialInputs } from './provenance.mjs';

export async function writePlan(env = process.env, root = process.cwd(), client = null) {
  const inputs = {
    definitions: env.INPUT_DEFINITIONS,
    repository: env.INPUT_REPOSITORY,
    ref: env.INPUT_REF,
  };
  if (healthEnabled(env.INPUT_HEALTH) && env.INPUT_MEDIA === 'false') throw new Error('Health requires media: true');
  const plan = planDefinitions(inputs, env, root);
  const resolver = client ?? githubProvenanceClient(env);
  const declared = readEvaluatorsFile(fileURLToPath(new URL('../evaluators.yml', import.meta.url)));
  for (const item of plan.definitions) {
    const notice = ignoredEvaluatorNotice(item.lock);
    if (notice) console.error(notice);
    if (item.execution === 'fixture') item.provenance = fixtureProvenance(item);
    else item.provenance = await assertPinnedInputs(item, resolver);
  }
  const health = healthEnabled(env.INPUT_HEALTH) ? await resolveHealth(resolver, env.INPUT_HEALTH_TRIAL ?? '1') : null;
  const verifierCount = plan.definitions.filter((item) => item.execution === 'capability-verifier').length;
  const serialised = {
    ...(health ? { health } : {}),
    evaluators: { lines: declared.lines, input: declared.input },
    robot: plan.robot,
    definitions: plan.definitions.map((item) => ({
      id: item.id,
      path: item.path,
      lockPath: item.lockPath,
      procedure: item.procedure,
      scenario: item.scenario,
      configurations: item.configurations,
      execution: item.execution,
      robot: item.robot,
      callerRepository: item.callerRepository,
      artifactPrefix: item.artifactPrefix,
      workflow: item.workflow,
      lock: item.lock,
      sources: item.sources ?? null,
      provenance: item.provenance,
    })),
  };
  const planPath = env.INPUT_PLAN_PATH || path.join(root, 'artifacts', 'verification-plan', 'plan.json');
  mkdirSync(path.dirname(planPath), { recursive: true });
  writeFileSync(planPath, `${JSON.stringify(serialised, null, 2)}\n`, 'utf8');
  const fixture = matrixFor(plan, 'fixture');
  const hallway = trialMatrix(plan, 'walk-hallway');
  const scenario = trialMatrix(plan, 'scenario');
  const outputs = {
    fixture: JSON.stringify(fixture),
    fixture_count: String(fixture.length),
    hallway: JSON.stringify(hallway),
    hallway_count: String(hallway.length),
    scenario: JSON.stringify(scenario),
    scenario_count: String(scenario.length),
    'plan-path': planPath,
    evaluators: declared.input,
    evaluators_run: declared.run && verifierCount > 0 ? 'true' : 'false',
  };
  writeGithubOutput(env, outputs);
  return { plan: serialised, outputs, planPath };
}

function trialMatrix(plan, procedure) {
  return matrixFor(plan, 'capability-verifier', procedure).map((row) => {
    const item = plan.definitions.find((entry) => entry.id === row.id);
    return { ...row, ...resolvedTrialInputs(item?.provenance) };
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const result = await writePlan();
    console.log(
      `Planned ${result.plan.definitions.length} definition(s) for ${result.plan.robot.repository}@${result.plan.robot.ref}`
    );
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}
