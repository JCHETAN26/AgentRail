// Fails when the committed TypeScript types no longer match openapi.json.
//
// Regenerating into a temporary file and comparing (rather than trusting a
// developer to re-run the generator) is what makes `ci / contracts` meaningful.

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const committed = join(packageRoot, 'src', 'generated', 'api.ts');
const scratch = mkdtempSync(join(tmpdir(), 'agentrail-contracts-'));
const regenerated = join(scratch, 'api.ts');

try {
  execFileSync('pnpm', ['exec', 'openapi-typescript', './openapi.json', '-o', regenerated], {
    cwd: packageRoot,
    stdio: 'inherit',
  });

  const expected = readFileSync(regenerated, 'utf8');
  const actual = readFileSync(committed, 'utf8');

  if (expected !== actual) {
    console.error(
      'packages/contracts/src/generated/api.ts is out of date with openapi.json.\n' +
        'Run: make contracts',
    );
    process.exit(1);
  }

  console.log('Generated API types are up to date.');
} finally {
  rmSync(scratch, { recursive: true, force: true });
}
