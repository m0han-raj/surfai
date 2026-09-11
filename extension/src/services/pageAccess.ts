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
 * Nothing may be awaited before the call. Chrome honours a permission prompt
 * only while the click that triggered it is still on the stack, and a single
 * await ends that: the button depresses, no prompt appears, and nothing
 * throws or logs. So this is deliberately not `async` above the call, and the
 * caller must reach it directly from an event handler.
 *
 * Whether access is already held is the caller's business for the same
 * reason. Asking here would cost an await, and the panel already knows, since
 * it renders a different button in that state.
 */
export function requestPageAccess(): Promise<boolean> {
  if (!available()) return Promise.resolve(false);

  try {
    return Promise.resolve(chrome.permissions.request(allUrls()))
      .then(Boolean)
      // Chrome rejects outright when there was no gesture after all. A
      // decline and a refusal to ask are the same outcome to the caller.
      .catch(() => false);
  } catch {
    return Promise.resolve(false);
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
