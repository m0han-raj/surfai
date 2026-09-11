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

export default function Suggestions({ insight, onPick }: SuggestionsProps) {
  const prompts = suggestionsFor(insight);

  return (
    <div className="welcome">
      <p className="welcome__title">How can I help?</p>
      <p className="welcome__sub">
        Ask me anything, or about the page you are on. I can act on it when you ask.
      </p>

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
