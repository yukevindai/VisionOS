import { cp, mkdir, rm } from 'node:fs/promises';
// Resolve from this file so the build also works when called from the repo root.
const root = new URL('./', import.meta.url);
const output = new URL('dist/', root);
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
for (const path of ['index.html', 'accessibility.html', 'styles.css', 'assets']) {
  await cp(new URL(path, root), new URL(path, output), { recursive: true });
}
console.log('Built VisionOS company website in apps/website/dist');
