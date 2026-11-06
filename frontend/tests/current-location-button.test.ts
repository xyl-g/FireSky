import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'vite';

test('CurrentLocationButton mounts without a global tooltip provider', async () => {
  const vite = await createServer({
    root: process.cwd(),
    server: { middlewareMode: true },
    configFile: `${process.cwd()}/vite.config.ts`,
  });

  try {
    const module = await vite.ssrLoadModule('/src/lib/components/CurrentLocationButton.svelte');
    // Use Vite's SSR-loaded Svelte runtime so the test exercises the same
    // component context implementation as the application bundle.
    const { render } = await vite.ssrLoadModule('svelte/server');
    const output = render(module.default, { props: { onlocate: () => undefined } });
    const html = output.body;

    assert.match(html, /使用当前位置/);
    assert.match(html, /aria-label="使用当前位置"/);
  } finally {
    await vite.close();
  }
});
