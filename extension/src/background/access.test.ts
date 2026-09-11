/**
 * Telling apart a page Chrome forbids from a page we simply have not been
 * allowed to read.
 *
 * These produced the same sentence, and the sentence blamed the page:
 * "SurfAI cannot read this page. Open a regular website and try again." On a
 * perfectly ordinary website, with the real cause being that `activeTab` only
 * covers the tab the panel was opened on. That message sent the debugging in
 * the wrong direction for a while, so the distinction is pinned here.
 */

import { describe, expect, it } from 'vitest';
import { explainInjectionFailure, isRestricted, restrictedMessage } from './access';

describe('isRestricted', () => {
  it('knows the pages Chrome will not let anyone script', () => {
    for (const url of [
      'chrome://extensions',
      'chrome-extension://abc/page.html',
      'edge://settings',
      'about:blank',
      'devtools://devtools/bundled/inspector.html',
      'view-source:https://example.com',
      'https://chromewebstore.google.com/detail/x',
    ]) {
      expect(isRestricted(url), url).toBe(true);
    }
  });

  it('treats an ordinary site as scriptable', () => {
    for (const url of ['https://example.com/a', 'http://localhost:3000/']) {
      expect(isRestricted(url), url).toBe(false);
    }
  });

  it('treats a missing url as restricted', () => {
    expect(isRestricted(undefined)).toBe(true);
  });
});

describe('explainInjectionFailure', () => {
  const denied = 'Cannot access contents of the page. Extension manifest must request permission';

  it('recognises a missing permission on an ordinary site', () => {
    const failure = explainInjectionFailure('https://news.example.com/a', denied);

    expect(failure.needsPermission).toBe(true);
    expect(failure.message).toMatch(/permission|access/i);
    // The page is not the problem, so the message must not imply it is.
    expect(failure.message).not.toMatch(/cannot read this page/i);
  });

  it('says what to do about it', () => {
    const failure = explainInjectionFailure('https://news.example.com/a', denied);
    expect(failure.message).toMatch(/settings/i);
  });

  it('still blames Chrome for a page Chrome really does forbid', () => {
    // Granting every permission in the world would not help here, so offering
    // a permission prompt would be a dead end.
    const failure = explainInjectionFailure('chrome://extensions', denied);

    expect(failure.needsPermission).toBe(false);
    expect(failure.message).toMatch(/browser settings pages/i);
  });

  it('does not offer a permission prompt for the Web Store', () => {
    const failure = explainInjectionFailure('https://chromewebstore.google.com/x', denied);
    expect(failure.needsPermission).toBe(false);
  });

  it('reports an unrecognised failure as itself rather than guessing', () => {
    const failure = explainInjectionFailure('https://example.com', 'The tab was closed.');

    expect(failure.needsPermission).toBe(false);
    expect(failure.message).toContain('The tab was closed.');
  });

  it('recognises the other wording Chrome uses', () => {
    // Chrome has more than one phrasing for the same refusal.
    for (const detail of [
      'Cannot access contents of url "https://x.com/". Extension manifest must request permission to access this host.',
      'This page cannot be scripted due to an ExtensionsSettings policy.',
    ]) {
      expect(explainInjectionFailure('https://x.com/', detail).message).toBeTruthy();
    }
  });
});

describe('restrictedMessage', () => {
  it('names the specific reason where there is one', () => {
    expect(restrictedMessage('chrome://settings')).toMatch(/browser settings/i);
    expect(restrictedMessage('https://chromewebstore.google.com/x')).toMatch(/Web Store/i);
    expect(restrictedMessage(undefined)).toMatch(/no active tab/i);
  });
});
