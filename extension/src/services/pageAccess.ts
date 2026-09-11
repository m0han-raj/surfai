/**
 * Permission to read pages as the user browses.
 *
 * `activeTab` grants access to a tab at the moment the user invokes the
 * extension, and to nothing they navigate to afterwards. That is the right
 * trade for a tool you point at one page; it is the wrong one for a panel that
 * is supposed to follow you between tabs, which is why the chat kept not
 * knowing which site was in front of it.
 *
 * Broad access is the only thing that fixes it, so it is declared optional and
 * asked for here: nothing is granted at install, the prompt appears when the
 * user presses a button, and Chrome's own Site access controls can take it
 * back. `activeTab` still covers the tab the panel was opened on, so declining
 * leaves a working, narrower SurfAI rather than a broken one.
 */

/**
 * Every site, because "the tab I am on" is not a pattern you can write.
 *
 * Built fresh each call: Chrome's typings take a mutable array, and a shared
 * literal would be handing the API something another caller could alter.
 */
function allUrls(): chrome.permissions.Permissions {
  return { origins: ['<all_urls>'] };
}

function available(): boolean {
  return typeof chrome !== 'undefined' && Boolean(chrome?.permissions);
}

/** Does SurfAI currently have access to pages generally? */
export async function hasPageAccess(): Promise<boolean> {
  if (!available()) return false;
  try {
    return Boolean(await chrome.permissions.contains(allUrls()));
  } catch {
    // Treated as "no": the caller offers to ask, which is safe either way.
    return false;
  }
}

/**
 * Ask for it. Resolves to whether SurfAI ended up with access.
 *
 * Must be called from a user gesture; Chrome rejects the prompt otherwise,
 * and that rejection is reported as a decline rather than allowed to escape.
 */
export async function requestPageAccess(): Promise<boolean> {
  if (!available()) return false;
  // Already granted: prompting again would flash a dialog that decides
  // nothing and make the button look broken.
  if (await hasPageAccess()) return true;

  try {
    return Boolean(await chrome.permissions.request(allUrls()));
  } catch {
    return false;
  }
}

/** Give it back. SurfAI falls back to the tab the panel was opened on. */
export async function revokePageAccess(): Promise<void> {
  if (!available()) return;
  try {
    await chrome.permissions.remove(allUrls());
  } catch {
    // Nothing to undo, or Chrome declined. Either way the caller re-reads the
    // real state rather than trusting this to have worked.
  }
}
