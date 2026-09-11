/**
 * React binding for the conversation.
 *
 * `AgentRunner` owns control flow; this hook owns what the user sees. Two
 * things shape the design:
 *
 * 1. Steps belong to a reply, not to a panel. While a task runs, a single
 *    pending assistant message collects the steps; when the answer arrives it
 *    replaces the placeholder in situ and keeps the steps folded underneath.
 *    A direct answer collects no steps, so it renders as plain chat.
 * 2. Confirmation uses a deferred promise, so the runner can simply `await` the
 *    user's decision rather than the hook duplicating its state machine.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AgentState, ChatMessage, Directive, MessageStep } from '../types/agent';
import { AgentRunner } from '../services/agent-runner';
import { getSettings } from '../services/storage';

/** Turns the backend sends back as context on a follow-up question. */
const HISTORY_TURNS = 8;

let counter = 0;
function nextId(prefix: string): string {
  counter += 1;
  return `${prefix}-${Date.now()}-${counter}`;
}

export function useAgent() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [state, setState] = useState<AgentState>('IDLE');
  const [running, setRunning] = useState(false);
  const [pendingConfirmation, setPendingConfirmation] = useState<Directive | null>(null);

  const confirmResolver = useRef<((approved: boolean) => void) | null>(null);
  const optionsRef = useRef({ maxElements: 60, actionTimeoutMs: 10_000 });
  /** Id of the assistant placeholder currently collecting steps. */
  const pendingIdRef = useRef<string | null>(null);

  useEffect(() => {
    void getSettings().then((settings) => {
      optionsRef.current = {
        maxElements: settings.maxElements,
        actionTimeoutMs: settings.actionTimeoutMs,
      };
    });
  }, []);

  /** Create (or reuse) the assistant placeholder that steps attach to. */
  const ensurePending = useCallback((): string => {
    if (pendingIdRef.current) return pendingIdRef.current;
    const id = nextId('a');
    pendingIdRef.current = id;
    setMessages((current) => [
      ...current,
      { id, role: 'assistant', content: '', at: Date.now(), pending: true, steps: [] },
    ]);
    return id;
  }, []);

  const addStep = useCallback(
    (label: string, status: MessageStep['status']) => {
      const id = ensurePending();
      setMessages((current) =>
        current.map((message) => {
          if (message.id !== id) return message;
          // Only one step is live at a time; close out the previous one.
          const settled = (message.steps ?? []).map((step) =>
            step.status === 'running' ? { ...step, status: 'done' as const } : step,
          );
          return {
            ...message,
            content: status === 'running' ? label : message.content,
            steps: [...settled, { id: nextId('s'), label, status }],
          };
        }),
      );
    },
    [ensurePending],
  );

  /** Replace the placeholder with the final reply, keeping its steps. */
  const settlePending = useCallback(
    (content: string, options: { error?: boolean; warnings?: string[] } = {}) => {
      const id = pendingIdRef.current;
      pendingIdRef.current = null;

      setMessages((current) => {
        if (id && current.some((m) => m.id === id)) {
          return current.map((message) =>
            message.id === id
              ? {
                  ...message,
                  content,
                  pending: false,
                  error: options.error,
                  warnings: options.warnings?.length ? options.warnings : undefined,
                  steps: (message.steps ?? []).map((step) =>
                    step.status === 'running' ? { ...step, status: 'done' as const } : step,
                  ),
                }
              : message,
          );
        }
        // A direct answer: no placeholder was ever created, so no steps.
        return [
          ...current,
          {
            id: nextId('a'),
            role: 'assistant',
            content,
            at: Date.now(),
            error: options.error,
            warnings: options.warnings?.length ? options.warnings : undefined,
          },
        ];
      });
    },
    [],
  );

  const runner = useMemo(() => {
    return new AgentRunner(
      {
        onDirective: (directive) => {
          setState(directive.state);
        },

        onActivity: addStep,

        onConfirmationRequired: (directive) => {
          setPendingConfirmation(directive);
          setState('WAITING_CONFIRMATION');
          return new Promise<boolean>((resolve) => {
            confirmResolver.current = resolve;
          });
        },

        onFinished: (directive) => {
          setState(directive.state);
          setRunning(false);
          setPendingConfirmation(null);
          settlePending(directive.message || 'Done.', {
            error: directive.type === 'error' && directive.state !== 'CANCELLED',
            warnings: directive.warnings,
          });
        },

        onError: (message) => {
          setRunning(false);
          setState('FAILED');
          setPendingConfirmation(null);
          settlePending(message, { error: true });
        },
      },
      optionsRef.current,
    );
  }, [addStep, settlePending]);

  const send = useCallback(
    async (text: string) => {
      const history = messages
        .filter((m) => !m.pending && m.content)
        .slice(-HISTORY_TURNS)
        .map((m) => ({ role: m.role, content: m.content }));

      setMessages((current) => [
        ...current,
        { id: nextId('u'), role: 'user', content: text, at: Date.now() },
      ]);
      setRunning(true);
      setState('ANALYZING');
      await runner.send(text, history);
    },
    [messages, runner],
  );

  const runFavourite = useCallback(
    async (favouriteId: string, request: string) => {
      setMessages((current) => [
        ...current,
        { id: nextId('u'), role: 'user', content: request, at: Date.now() },
      ]);
      setRunning(true);
      setState('ANALYZING');
      await runner.runFavourite(favouriteId, request);
    },
    [runner],
  );

  const stop = useCallback(async () => {
    // Release a pending confirmation first, or the loop stays parked on it.
    confirmResolver.current?.(false);
    confirmResolver.current = null;
    setPendingConfirmation(null);
    await runner.stop();
    setRunning(false);
    setState('CANCELLED');
  }, [runner]);

  const answerConfirmation = useCallback((approved: boolean) => {
    setPendingConfirmation(null);
    const resolve = confirmResolver.current;
    confirmResolver.current = null;
    resolve?.(approved);
  }, []);

  const clear = useCallback(() => {
    pendingIdRef.current = null;
    setMessages([]);
    setState('IDLE');
  }, []);

  return {
    messages,
    state,
    running,
    pendingConfirmation,
    send,
    runFavourite,
    stop,
    answerConfirmation,
    clear,
  };
}
