import { resolve } from 'node:path';
import { defineConfig } from 'vite';

/**
 * Second build pass: the content script only.
 *
 * A content script runs in the page's world with no module loader, so it must
 * be one self-contained IIFE. `emptyOutDir` is false because the first pass
 * has already written the side panel, worker and manifest into `dist/`.
 */
export default defineConfig({
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
      '@shared': resolve(__dirname, '../shared'),
    },
  },
  build: {
    outDir: resolve(__dirname, 'dist'),
    emptyOutDir: false,
    minify: false,
    sourcemap: true,
    target: 'chrome120',
    rollupOptions: {
      input: { content: resolve(__dirname, 'src/content/index.ts') },
      output: {
        format: 'iife',
        entryFileNames: 'content.js',
        inlineDynamicImports: true,
      },
    },
  },
});
