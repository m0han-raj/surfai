import { resolve } from 'node:path';
import { copyFileSync, mkdirSync, readFileSync, existsSync, renameSync, rmSync } from 'node:fs';
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

      const manifestPath = resolve(__dirname, 'public/manifest.json');
      copyFileSync(manifestPath, resolve(outDir, 'manifest.json'));

      // Copy only the icons the manifest actually declares. public/icons also
      // holds store-listing sizes and the SVG source, which have no business
      // being shipped inside the extension package.
      const manifest = JSON.parse(readFileSync(manifestPath, 'utf-8'));
      const declared = Object.values(manifest.icons ?? {}) as string[];
      if (declared.length > 0) {
        mkdirSync(resolve(outDir, 'icons'), { recursive: true });
        for (const relative of declared) {
          const from = resolve(__dirname, 'public', relative);
          if (!existsSync(from)) {
            throw new Error(`manifest.json declares ${relative}, which does not exist`);
          }
          copyFileSync(from, resolve(outDir, relative));
        }
      }
    },
  };
}

export default defineConfig({
  // Vite would otherwise copy all of public/ into dist. public/icons holds
  // store-listing sizes and SVG source that must not ship inside the
  // extension, so the copy is done explicitly and filtered instead.
  publicDir: false,
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
