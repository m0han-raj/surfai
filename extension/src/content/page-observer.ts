/**
 * Page-change detection.
 *
 * After an action the agent needs to know whether anything actually happened.
 * Many sites navigate without a page load (SPA routing) or replace results
 * in place, so neither a load event nor a URL comparison is sufficient. A
 * MutationObserver on the subtree catches both.
 */

export interface PageChangeObserver {
  /** True if the DOM changed materially since the observer started. */
  changed: () => boolean;
  /** Number of mutation records seen. */
  count: () => number;
  stop: () => void;
}

/** Mutations that are noise rather than a real content change. */
const NOISE_ATTRIBUTES = new Set(['style', 'class', 'data-focus-visible-added']);

export function observePageChange(target: Node = document.body): PageChangeObserver {
  let mutations = 0;
  let meaningful = false;

  if (typeof MutationObserver === 'undefined' || !target) {
    return { changed: () => false, count: () => 0, stop: () => undefined };
  }

  const observer = new MutationObserver((records) => {
    for (const record of records) {
      mutations += 1;
      if (record.type === 'childList' && (record.addedNodes.length || record.removedNodes.length)) {
        meaningful = true;
      } else if (
        record.type === 'attributes' &&
        record.attributeName &&
        !NOISE_ATTRIBUTES.has(record.attributeName)
      ) {
        meaningful = true;
      } else if (record.type === 'characterData') {
        meaningful = true;
      }
    }
  });

  observer.observe(target, {
    childList: true,
    subtree: true,
    attributes: true,
    characterData: true,
  });

  return {
    changed: () => meaningful,
    count: () => mutations,
    stop: () => observer.disconnect(),
  };
}

/**
 * Resolve once the page looks settled, or after `timeoutMs`.
 *
 * Used before capturing a snapshot so the agent does not observe a page
 * mid-render and plan against elements that are about to be replaced.
 */
export function waitForSettle(timeoutMs = 2000, quietMs = 350): Promise<boolean> {
  return new Promise((resolve) => {
    if (typeof MutationObserver === 'undefined' || !document.body) {
      resolve(true);
      return;
    }

    let quietTimer: number | undefined;
    const deadline = window.setTimeout(() => {
      cleanup();
      resolve(false);
    }, timeoutMs);

    const observer = new MutationObserver(() => {
      window.clearTimeout(quietTimer);
      quietTimer = window.setTimeout(finish, quietMs);
    });

    function cleanup() {
      observer.disconnect();
      window.clearTimeout(quietTimer);
      window.clearTimeout(deadline);
    }

    function finish() {
      cleanup();
      resolve(true);
    }

    observer.observe(document.body, { childList: true, subtree: true });
    quietTimer = window.setTimeout(finish, quietMs);
  });
}

/** True once the document has finished its initial parse. */
export function documentReady(): Promise<void> {
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    document.addEventListener('DOMContentLoaded', () => resolve(), { once: true });
  });
}
