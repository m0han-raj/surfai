import type { PageInsight } from '../usePageContext';

interface SuggestionsProps {
  insight: PageInsight | null;
  onPick: (prompt: string) => void;
}

/**
 * Starter prompts for an empty conversation.
 *
 * Chosen from what the current page actually supports, so the suggestions are
 * things SurfAI can really do here rather than a fixed advert for its features.
 * On a page it cannot read, the suggestions fall back to general assistance,
 * which is honest about what is available.
 */
export function suggestionsFor(insight: PageInsight | null): string[] {
  if (!insight) {
    return ['Explain a concept to me', 'Help me draft a message', 'What can you do?'];
  }

  const picks: string[] = [];
  const { capabilities, pageType } = insight;

  if (pageType === 'article' || pageType === 'content') {
    picks.push('Summarise this page');
  } else {
    picks.push('What is on this page?');
  }

  if (capabilities.search) picks.push('Find something on this site');
  if (capabilities.results && !capabilities.search) picks.push('List what is shown here');
  if (capabilities.filters && picks.length < 3) picks.push('What filters are available?');

  picks.push('Save this page for later');

  return picks.slice(0, 3);
}

export interface Readiness {
  ready: boolean;
  title: string;
  subtitle: string;
}

/**
 * What the panel says before you have asked anything.
 *
 * It used to open with "How can I help?", which is friendly and tells you
 * nothing. The question actually on your mind when you open a side panel is
 * whether it can see the page you are looking at, and the honest answer varies:
 * a chrome:// tab cannot be read at all, and a page that tried to smuggle
 * instructions is worth knowing about before you trust an answer about it.
 * Naming what it read, and how much of it, lets you judge the answers.
 */
export function readinessFor(insight: PageInsight | null): Readiness {
  if (!insight) {
    return {
      ready: false,
      title: 'How can I help?',
      subtitle:
        'I cannot read this page, so ask me anything else. Open a website and I will read it.',
    };
  }

  const name = insight.title || insight.domain || 'this page';
  const subtitle = insight.suspicious
    ? 'Careful: this page tried to give me instructions. I ignored them, but treat what ' +
      'it says with suspicion.'
    : `${insight.elementCount} things on it I can see and act on. Ask me about it.`;

  return { ready: true, title: `I have read ${name}`, subtitle };
}

export default function Suggestions({ insight, onPick }: SuggestionsProps) {
  const prompts = suggestionsFor(insight);
  const readiness = readinessFor(insight);

  return (
    <div className="welcome">
      <p className="welcome__title">{readiness.title}</p>
      <p className="welcome__sub">{readiness.subtitle}</p>

      <ul className="welcome__list">
        {prompts.map((prompt) => (
          <li key={prompt}>
            <button type="button" className="suggestion" onClick={() => onPick(prompt)}>
              {prompt}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
