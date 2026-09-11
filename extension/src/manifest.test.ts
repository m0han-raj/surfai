/**
 * Manifest invariants.
 *
 * These guard properties that are easy to break silently and expensive to
 * notice: a narrowed connect-src makes the Settings "backend address" field
 * non-functional, and a widened host permission hands SurfAI access to every
 * site the user visits.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const manifest = JSON.parse(
  readFileSync(resolve(__dirname, '../public/manifest.json'), 'utf-8'),
) as {
  manifest_version: number;
  permissions: string[];
  host_permissions: string[];
  background: { service_worker: string; type: string };
  side_panel: { default_path: string };
  content_security_policy: { extension_pages: string };
};

describe('extension manifest', () => {
  it('is Manifest V3 with a module service worker', () => {
    expect(manifest.manifest_version).toBe(3);
    expect(manifest.background.type).toBe('module');
  });

  it('requests only the permissions SurfAI actually uses', () => {
    expect(manifest.permissions.sort()).toEqual(
      ['activeTab', 'scripting', 'sidePanel', 'storage', 'tabs'].sort(),
    );
  });

  it('never requests access to every site', () => {
    const serialised = JSON.stringify(manifest);
    expect(serialised).not.toContain('<all_urls>');
    expect(serialised).not.toContain('*://*/*');
    expect(serialised).not.toContain('http://*/*');
    expect(serialised).not.toContain('https://*/*');
  });

  it('grants host access to the local backend only', () => {
    expect(manifest.host_permissions).toEqual([
      'http://localhost/*',
      'http://127.0.0.1/*',
    ]);
  });

  it('allows the backend on any local port, so Settings can change it', () => {
    // Pinning a single port here silently breaks the Settings field for anyone
    // whose 8000 is already taken.
    const csp = manifest.content_security_policy.extension_pages;
    expect(csp).toContain('http://localhost:*');
    expect(csp).toContain('http://127.0.0.1:*');
    expect(csp).not.toMatch(/localhost:\d+/);
  });

  it('keeps a restrictive script policy', () => {
    const csp = manifest.content_security_policy.extension_pages;
    expect(csp).toContain("script-src 'self'");
    expect(csp).not.toContain('unsafe-eval');
    expect(csp).not.toContain('unsafe-inline');
  });
});
