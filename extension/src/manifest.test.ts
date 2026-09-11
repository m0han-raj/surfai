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

  it('grants host access only to localhost and the one known backend', () => {
    expect(manifest.host_permissions).toEqual([
      'http://localhost/*',
      'http://127.0.0.1/*',
      'https://surfai-iota.vercel.app/*',
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

  it('can reach the hosted backend', () => {
    // MV3 blocks any origin absent from connect-src, with no visible error, so
    // a backend address the user sets in Settings is unreachable unless it is
    // named here. Adding a host is therefore a deliberate manifest change.
    const csp = manifest.content_security_policy.extension_pages;
    expect(csp).toContain('https://surfai-iota.vercel.app');

    const permitted = manifest.host_permissions.join(' ');
    expect(permitted).toContain('surfai-iota.vercel.app');
  });

  it('does not open connect-src to arbitrary origins', () => {
    const csp = manifest.content_security_policy.extension_pages;
    expect(csp).not.toContain('https://*');
    expect(csp).not.toContain('*.vercel.app');
  });

  it('declares an OAuth client with the narrowest useful scopes', () => {
    // Identity comes from the token's subject; email is for display and the
    // optional allowlist. Anything more means a scarier consent screen.
    const oauth = (manifest as unknown as { oauth2?: { client_id: string; scopes: string[] } })
      .oauth2;
    expect(oauth?.client_id).toMatch(/\.apps\.googleusercontent\.com$/);
    expect(oauth?.client_id).not.toContain('placeholder');
    expect(oauth?.scopes.sort()).toEqual(['email', 'openid']);
  });

  it('keeps a restrictive script policy', () => {
    const csp = manifest.content_security_policy.extension_pages;
    expect(csp).toContain("script-src 'self'");
    expect(csp).not.toContain('unsafe-eval');
    expect(csp).not.toContain('unsafe-inline');
  });
});
