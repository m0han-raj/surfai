import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Square } from 'lucide-react';
import { getDraft, saveDraft } from '../../services/storage';

interface InputBoxProps {
  onSubmit: (message: string) => void;
  onStop: () => void;
  running: boolean;
  disabled?: boolean;
  placeholder?: string;
}

/**
 * The composer.
 *
 * Enter sends, Shift+Enter adds a line. The draft is persisted so closing the
 * side panel mid-thought does not lose what was typed.
 */
export default function InputBox({
  onSubmit,
  onStop,
  running,
  disabled = false,
  placeholder = 'Ask anything about this page...',
}: InputBoxProps) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    getDraft().then((draft) => {
      if (draft) setValue(draft);
    });
  }, []);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 140)}px`;
  }, [value]);

  function submit() {
    const message = value.trim();
    if (!message || running || disabled) return;
    onSubmit(message);
    setValue('');
    void saveDraft('');
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <div className="composer">
      <form
        className="composer__field"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <label htmlFor="surfai-input" className="sr-only">
          Ask SurfAI about this page
        </label>
        <textarea
          id="surfai-input"
          ref={textareaRef}
          className="composer__input"
          rows={1}
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(event) => {
            setValue(event.target.value);
            void saveDraft(event.target.value);
          }}
          onKeyDown={handleKeyDown}
        />

        {running ? (
          <button
            type="button"
            className="button button--danger button--sm"
            onClick={onStop}
            aria-label="Stop the running task"
          >
            <Square size={12} aria-hidden="true" />
            Stop
          </button>
        ) : (
          <button
            type="submit"
            className="button button--primary button--sm"
            disabled={!value.trim() || disabled}
            aria-label="Send message"
          >
            <ArrowUp size={12} aria-hidden="true" />
          </button>
        )}
      </form>

      <p className="composer__hint">
        {running
          ? 'SurfAI is working. Press Stop to cancel.'
          : 'Enter to send, Shift+Enter for a new line.'}
      </p>
    </div>
  );
}
