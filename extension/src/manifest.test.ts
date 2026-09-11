/**
 * Manifest invariants.
 *
 * These guard properties that are easy to break silently and expensive to
 * notice: a narrowed connect-src makes the Settings "backend address" field
 * non-functional, and a host permission that moves from optional to granted
 * hands SurfAI access to every site the user visits without them agreeing to
 * it.
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
  optional_host_permissions: string[];
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

  it('never takes access to every site at install', () => {
    // This used to forbid <all_urls> outright. That was the right rule while
    // SurfAI only read the page you pointed it at, and the wrong one once the
    // panel had to follow you between tabs: `activeTab` covers the tab the
    // panel was opened on and nothing you navigate to after, so the chat kept
    // losing track of which site you were looking at.
    //
    // What survives of the rule is the part that matters. Nothing broad is
    // granted by installing SurfAI. Broad access is asked for from a button,
    // and Chrome's Site access controls can take it back.
    const granted = JSON.stringify({
      permissions: manifest.permissions,
      host_permissions: manifest.host_permissions,
    });

    for (const pattern of ['<all_urls>', '*://*/*', 'http://*/*', 'https://*/*']) {
      expect(granted, `${pattern} must not be granted at install`).not.toContain(pattern);
    }
  });

  it('asks for broad access rather than assuming it', () => {
    expect(manifest.optional_host_permissions).toEqual(['<all_urls>']);
  });

  it('keeps activeTab, so declining leaves a narrower SurfAI and not a broken one', () => {
    // The tab the panel was opened on still works with no grant at all.
    expect(manifest.permissions).toContain('activeTab');
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
