/**
 * Why SurfAI could not read a page, said accurately.
 *
 * Two very different situations produce the same refusal from
 * `chrome.scripting`: a page Chrome forbids anyone from scripting, and an
 * ordinary page the user has simply not granted access to. They used to share
 * a message, and the message blamed the page. One of them is fixable by the
 * user in a single click, so conflating them costs real time.
 *
 * Kept apart from the service worker so it can be tested without a browser.
 */

/** Pages where Chrome refuses content-script injection, permissions or not. */
const RESTRICTED_PREFIXES = [
  'chrome://',
  'chrome-extension://',
  'edge://',
  'about:',
  'devtools://',
  'view-source:',
  'https://chromewebstore.google.com',
  'https://chrome.google.com/webstore',
];

/** Chrome's various phrasings for "you were not granted this host". */
const PERMISSION_DENIED =
  /must request permission|cannot be scripted|cannot access contents/i;

export function isRestricted(url: string | undefined): boolean {
  if (!url) return true;
  return RESTRICTED_PREFIXES.some((prefix) => url.startsWith(prefix));
}

export function restrictedMessage(url: string | undefined): string {
  if (!url) return 'No active tab was found.';
  if (url.startsWith('chrome://') || url.startsWith('edge://') || url.startsWith('about:')) {
    return 'SurfAI cannot read browser settings pages. Open a website and try again.';
  }
  if (url.includes('chromewebstore') || url.includes('chrome.google.com/webstore')) {
    return 'Chrome blocks extensions from reading the Web Store. Open another site and try again.';
  }
  return 'SurfAI cannot read this page. Open a regular website and try again.';
}

export interface InjectionFailure {
  message: string;
  /** The user can fix this by granting access; worth offering the prompt. */
  needsPermission: boolean;
}

/**
 * Turn a `chrome.scripting` failure into something worth showing.
 *
 * `needsPermission` is the useful half: it says whether a prompt would help.
 * On a page Chrome forbids, every permission in the world changes nothing, so
 * offering one there would be a dead end dressed up as a fix.
 */
export function explainInjectionFailure(
  url: string | undefined,
  detail: string,
): InjectionFailure {
  if (isRestricted(url)) {
    return { message: restrictedMessage(url), needsPermission: false };
  }

  if (PERMISSION_DENIED.test(detail)) {
    return {
      message:
        'SurfAI has not been given access to this site. Open Settings and turn on ' +
        'page access to let it read pages as you browse.',
      needsPermission: true,
    };
  }

  return { message: `SurfAI could not attach to this page: ${detail}`, needsPermission: false };
}
