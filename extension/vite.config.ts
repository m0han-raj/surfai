import { resolve } from 'node:path';
import { copyFileSync, mkdirSync, readdirSync, existsSync, renameSync, rmSync } from 'node:fs';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Bundles the side panel and the service worker.
 *
 * Chrome loads SurfAI's code in three different worlds, and they cannot share a
 * Rollup graph:
 *
 *  - sidepanel      - a normal document; code-split React is fine
 *  - service-worker - an ES module worker; must be one file at a stable path
 *  - content        - injected into the page; must be a single IIFE
 *
 * The content script therefore builds in a second pass via
 * `vite.config.content.ts`. `npm run build` runs both.
 */

const outDir = resolve(__dirname, 'dist');

/**
 * Finalise the extension package: flatten the side panel HTML and copy static
 * assets.
 *
 * Rollup emits an HTML entry at its source path (`dist/src/sidepanel/`), but a
 * manifest wants a stable, shallow path. Asset references inside are absolute
 * (`/assets/...`), which resolve from the extension root, so the file can simply
 * be moved.
 */
function copyStaticAssets() {
  return {
    name: 'surfai-package-extension',
    closeBundle() {
      mkdirSync(outDir, { recursive: true });

      const emitted = resolve(outDir, 'src/sidepanel/index.html');
      if (existsSync(emitted)) {
        renameSync(emitted, resolve(outDir, 'sidepanel.html'));
        rmSync(resolve(outDir, 'src'), { recursive: true, force: true });
      }

      copyFileSync(resolve(__dirname, 'public/manifest.json'), resolve(outDir, 'manifest.json'));

      const iconsSrc = resolve(__dirname, 'public/icons');
      if (existsSync(iconsSrc)) {
        const iconsOut = resolve(outDir, 'icons');
        mkdirSync(iconsOut, { recursive: true });
        for (const file of readdirSync(iconsSrc)) {
          copyFileSync(resolve(iconsSrc, file), resolve(iconsOut, file));
        }
      }
    },
  };
}

export default defineConfig({
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
      '@shared': resolve(__dirname, '../shared'),
    },
  },
  plugins: [react(), copyStaticAssets()],
  build: {
    outDir,
    emptyOutDir: true,
    minify: false,
    sourcemap: true,
    target: 'chrome120',
    rollupOptions: {
      input: {
        sidepanel: resolve(__dirname, 'src/sidepanel/index.html'),
        'service-worker': resolve(__dirname, 'src/background/service-worker.ts'),
      },
      output: {
        format: 'es',
        entryFileNames: (chunk) =>
          chunk.name === 'service-worker' ? 'service-worker.js' : 'assets/[name].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name][extname]',
      },
    },
  },
});
