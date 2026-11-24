// SPDX-License-Identifier: Apache-2.0
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { matrixFor, planDefinitions } from './definitions.mjs';
import { githubProvenanceClient } from './github-provenance.mjs';
import { writeGithubOutput } from './github-output.mjs';
import { assertPinnedInputs, fixtureProvenance } from './provenance.mjs';

export async function writePlan(env = process.env, root = process.cwd(), client = null) {
  const inputs = {
    definitions: env.INPUT_DEFINITIONS,
    repository: env.INPUT_REPOSITORY,
    ref: env.INPUT_REF,
  };
  const plan = planDefinitions(inputs, env, root);
  const resolver = client ?? githubProvenanceClient(env);
  for (const item of plan.definitions) {
    if (item.execution === 'fixture') item.provenance = fixtureProvenance(item);
    else item.provenance = await assertPinnedInputs(item, resolver);
  }
  const serialised = {
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
  const hallway = matrixFor(plan, 'capability-verifier', 'walk-hallway');
  const scenario = matrixFor(plan, 'capability-verifier', 'scenario');
  const outputs = {
    fixture: JSON.stringify(fixture),
    fixture_count: String(fixture.length),
    hallway: JSON.stringify(hallway),
    hallway_count: String(hallway.length),
    scenario: JSON.stringify(scenario),
    scenario_count: String(scenario.length),
    'plan-path': planPath,
  };
  writeGithubOutput(env, outputs);
  return { plan: serialised, outputs, planPath };
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
