import { copyFileSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const plugin = dirname(dirname(fileURLToPath(import.meta.url)));
const source = join(plugin, '..', '..', '..', 'src', 'auto_research');
const destination = join(plugin, 'python', 'auto_research');
rmSync(destination, { recursive: true, force: true });
mkdirSync(destination, { recursive: true });
for (const file of [
  '__init__.py',
  'artifacts.py',
  'errors.py',
  'migration.py',
  'maintenance_cli.py',
  'memory_store.py',
  'native_store.py',
  'query_store.py',
  'schema5.py',
  'schema6.py',
  'workflow_store.py',
  'service.py',
]) {
  copyFileSync(join(source, file), join(destination, file));
}
