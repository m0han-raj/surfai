/**
 * Element classification and labelling.
 *
 * Turns a DOM node into the small set of facts the agent actually needs:
 * what kind of control it is, what a human would call it, and whether it can
 * be interacted with right now.
 */

import type { SemanticElement, SemanticElementType } from '@shared/types';

/** Inputs whose values must never leave the page. */
const SENSITIVE_INPUT_TYPES = new Set(['password', 'hidden']);

const SENSITIVE_NAME =
  /(pass(word|wd)?|pwd|secret|token|api[ _-]?key|otp|cvv|cvc|card[ _-]?(number|no)|ssn|aadhaar|security[ _-]?code|credit[ _-]?card)/i;

/** Roles that behave like buttons even on a div or span. */
const BUTTON_ROLES = new Set(['button', 'tab', 'menuitem', 'switch']);

const INTERACTIVE_SELECTOR = [
  'a[href]',
  'button',
  'input',
  'textarea',
  'select',
  '[role="button"]',
  '[role="link"]',
  '[role="tab"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[role="combobox"]',
  '[role="searchbox"]',
  '[role="menuitem"]',
  '[role="switch"]',
  '[contenteditable="true"]',
  '[onclick]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

// Headings only. A <label> is deliberately excluded: `labelFor` already
// attaches its text to the control it names, so capturing it separately
// duplicates information and spends budget dense pages need for controls.
export const STRUCTURAL_SELECTOR = 'h1,h2,h3,h4,h5,h6,[role="heading"]';

export function interactiveSelector(): string {
  return INTERACTIVE_SELECTOR;
}

/**
 * Is the element actually on screen and usable?
 *
 * Checked against layout, not just CSS: a control with zero area, or one moved
 * off-canvas, is invisible to a user and must be invisible to the agent too --
 * otherwise the planner targets things a human could never click.
 */
export function isVisible(element: Element): boolean {
  const el = element as HTMLElement;

  if (!el.isConnected) return false;
  if (el.hasAttribute('hidden')) return false;
  if (el.getAttribute('aria-hidden') === 'true') return false;

  const style = window.getComputedStyle(el);
  if (
    style.display === 'none' ||
    style.visibility === 'hidden' ||
    style.visibility === 'collapse' ||
    parseFloat(style.opacity || '1') < 0.05
  ) {
    return false;
  }

  const rect = el.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) {
    // A zero-size wrapper can still hold a visible child (common for icon
    // buttons), so fall back to checking for any laid-out client rect.
    if (el.getClientRects().length === 0) return false;
  }

  // Entirely outside the document, e.g. the off-screen-label pattern.
  if (rect.bottom < -window.innerHeight || rect.right < -window.innerWidth) {
    return false;
  }

  return true;
}

export function isDisabled(element: Element): boolean {
  const el = element as HTMLInputElement;
  return (
    el.disabled === true ||
    el.getAttribute('aria-disabled') === 'true' ||
    el.hasAttribute('disabled')
  );
}

/** True when the element's value is a credential and must not be captured. */
export function isSensitive(element: Element): boolean {
  const el = element as HTMLInputElement;
  const inputType = (el.getAttribute('type') || '').toLowerCase();
  if (SENSITIVE_INPUT_TYPES.has(inputType)) return true;

  const haystack = [
    el.getAttribute('name'),
    el.getAttribute('id'),
    el.getAttribute('aria-label'),
    el.getAttribute('placeholder'),
    el.getAttribute('autocomplete'),
  ]
    .filter(Boolean)
    .join(' ');

  return SENSITIVE_NAME.test(haystack);
}

export function classify(element: Element): SemanticElementType | null {
  const tag = element.tagName.toLowerCase();
  const role = (element.getAttribute('role') || '').toLowerCase();

  switch (tag) {
    case 'a':
      return element.hasAttribute('href') ? 'link' : null;
    case 'button':
      return 'button';
    case 'textarea':
      return 'textarea';
    case 'select':
      return 'select';
    case 'h1':
    case 'h2':
    case 'h3':
    case 'h4':
    case 'h5':
    case 'h6':
      return 'heading';
    case 'input': {
      const type = (element.getAttribute('type') || 'text').toLowerCase();
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'button' || type === 'submit' || type === 'reset') return 'button';
      if (type === 'image') return 'button';
      return 'input';
    }
    default:
      break;
  }

  if (role === 'heading') return 'heading';
  if (BUTTON_ROLES.has(role)) return 'button';
  if (role === 'link') return 'link';
  if (role === 'checkbox') return 'checkbox';
  if (role === 'radio') return 'radio';
  if (role === 'combobox' || role === 'listbox') return 'select';
  if (role === 'searchbox' || role === 'textbox') return 'input';
  if (element.getAttribute('contenteditable') === 'true') return 'textarea';
  if (element.hasAttribute('onclick') || element.hasAttribute('tabindex')) return 'button';

  return null;
}

/**
 * The label a human would use for this control.
 *
 * Preference order matters: an explicit accessible name beats visible text,
 * which beats a nearby <label>, which beats the placeholder. Getting this wrong
 * is the most common reason an agent cannot find "the Search button".
 */
export function labelFor(element: Element): string {
  const el = element as HTMLElement;

  const ariaLabel = el.getAttribute('aria-label');
  if (ariaLabel?.trim()) return ariaLabel.trim();

  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy) {
    const text = labelledBy
      .split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent?.trim() || '')
      .filter(Boolean)
      .join(' ');
    if (text) return text;
  }

  if (el.id) {
    // Scanning labels beats building a selector: it needs no CSS.escape (absent
    // in some runtimes) and cannot be broken by an id containing quotes.
    const explicit = Array.from(document.getElementsByTagName('label')).find(
      (label) => label.htmlFor === el.id || label.getAttribute('for') === el.id,
    );
    if (explicit?.textContent?.trim()) return explicit.textContent.trim();
  }

  const wrapping = el.closest('label');
  if (wrapping?.textContent?.trim()) return wrapping.textContent.trim();

  const text = visibleText(el);
  if (text) return text;

  const placeholder = el.getAttribute('placeholder');
  if (placeholder?.trim()) return placeholder.trim();

  const title = el.getAttribute('title');
  if (title?.trim()) return title.trim();

  const value = (el as HTMLInputElement).value;
  if (typeof value === 'string' && value.trim() && !isSensitive(el)) {
    return value.trim();
  }

  const alt = el.querySelector('img[alt]')?.getAttribute('alt');
  if (alt?.trim()) return alt.trim();

  return '';
}

/** Text content with whitespace collapsed and length capped. */
export function visibleText(element: Element, maxLength = 120): string {
  const text = (element.textContent || '').replace(/\s+/g, ' ').trim();
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
}

/** Build the wire representation of one element. */
export function describe(
  element: Element,
  id: string,
  type: SemanticElementType,
): SemanticElement {
  const el = element as HTMLInputElement;
  const tag = element.tagName.toLowerCase();
  const sensitive = isSensitive(element);

  const descriptor: SemanticElement = {
    id,
    type,
    tag,
    visible: isVisible(element),
    disabled: isDisabled(element),
  };

  const label = labelFor(element);
  if (label) descriptor.text = label;

  const placeholder = element.getAttribute('placeholder');
  if (placeholder) descriptor.placeholder = placeholder.slice(0, 120);

  const ariaLabel = element.getAttribute('aria-label');
  if (ariaLabel) descriptor.ariaLabel = ariaLabel.slice(0, 120);

  const name = element.getAttribute('name');
  if (name) descriptor.name = name.slice(0, 80);

  const role = element.getAttribute('role');
  if (role) descriptor.role = role;

  if (tag === 'input') {
    descriptor.inputType = (element.getAttribute('type') || 'text').toLowerCase();
  }

  if (type === 'heading') {
    const level = /^h([1-6])$/.exec(tag);
    descriptor.level = level ? Number(level[1]) : Number(element.getAttribute('aria-level')) || 2;
  }

  if (tag === 'a') {
    const href = element.getAttribute('href');
    if (href) {
      try {
        descriptor.href = new URL(href, window.location.href).href.slice(0, 300);
      } catch {
        descriptor.href = href.slice(0, 300);
      }
    }
  }

  if (tag === 'select') {
    descriptor.options = Array.from((element as unknown as HTMLSelectElement).options)
      .slice(0, 40)
      .map((option) => (option.textContent || option.value || '').trim().slice(0, 60))
      .filter(Boolean);
    if (el.value) descriptor.value = String(el.value).slice(0, 80);
  } else if (type === 'checkbox' || type === 'radio') {
    descriptor.value = el.checked ? 'checked' : 'unchecked';
  } else if ((type === 'input' || type === 'textarea') && !sensitive) {
    // Current contents matter: the agent must know a field already holds text.
    const value = typeof el.value === 'string' ? el.value : '';
    if (value) descriptor.value = value.slice(0, 120);
  }

  if (sensitive) {
    // Never carry the value; the structure alone is enough to act on it.
    delete descriptor.value;
    (descriptor as SemanticElement & { sensitive?: boolean }).sensitive = true;
  }

  return descriptor;
}
