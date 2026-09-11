/**
 * React binding for the agent loop.
 *
 * `AgentRunner` owns the control flow; this hook owns what the user sees.
 * Confirmation is handled with a deferred promise so the runner can simply
 * `await` the user's decision -- the loop pauses, the dialog opens, and the
 * answer resumes it, with no polling or state machine duplication.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ActivityEntry, AgentState, ChatMessage, Directive } from '../types/agent';
import { AgentRunner } from '../services/agent-runner';
import { getSettings } from '../services/storage';

let messageCounter = 0;
function nextId(prefix: string): string {
  messageCounter += 1;
  return `${prefix}-${Date.now()}-${messageCounter}`;
}

export function useAgent() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  const [state, setState] = useState<AgentState>('IDLE');
  const [step, setStep] = useState(0);
  const [maxSteps, setMaxSteps] = useState(0);
  const [running, setRunning] = useState(false);
  const [pendingConfirmation, setPendingConfirmation] = useState<Directive | null>(null);

  // Resolver for the in-flight confirmation promise.
  const confirmResolver = useRef<((approved: boolean) => void) | null>(null);
  const runnerRef = useRef<AgentRunner | null>(null);
  const optionsRef = useRef({ maxElements: 60, actionTimeoutMs: 10_000 });

  useEffect(() => {
    void getSettings().then((settings) => {
      optionsRef.current = {
        maxElements: settings.maxElements,
        actionTimeoutMs: settings.actionTimeoutMs,
      };
    });
  }, []);

  const addMessage = useCallback((message: Omit<ChatMessage, 'id' | 'at'>) => {
    setMessages((current) => [...current, { ...message, id: nextId('m'), at: Date.now() }]);
  }, []);

  const pushActivity = useCallback(
    (label: string, status: ActivityEntry['status']) => {
      setActivity((current) => {
        // Close out whatever was running: only one step is live at a time.
        const settled = current.map((entry) =>
          entry.status === 'running' ? { ...entry, status: 'done' as const } : entry,
        );
        return [...settled, { id: nextId('a'), label, status, at: Date.now() }].slice(-30);
      });
    },
    [],
  );

  const runner = useMemo(() => {
    const instance = new AgentRunner(
      {
        onDirective: (directive) => {
          setState(directive.state);
          setStep(directive.step);
          setMaxSteps(directive.max_steps);
          if (directive.activity && directive.type === 'action') {
            pushActivity(directive.activity, 'running');
          }
        },

        onActivity: pushActivity,

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
          if (directive.message) {
            addMessage({
              role: 'assistant',
              content: directive.message,
              warnings: directive.warnings?.length ? directive.warnings : undefined,
              error: directive.type === 'error' && directive.state !== 'CANCELLED',
            });
          }
        },

        onError: (message) => {
          setRunning(false);
          setState('FAILED');
          setPendingConfirmation(null);
          addMessage({ role: 'assistant', content: message, error: true });
        },
      },
      optionsRef.current,
    );
    runnerRef.current = instance;
    return instance;
  }, [addMessage, pushActivity]);

  const send = useCallback(
    async (text: string) => {
      addMessage({ role: 'user', content: text });
      setActivity([]);
      setRunning(true);
      setState('ANALYZING');
      await runner.send(text);
    },
    [addMessage, runner],
  );

  const runFavourite = useCallback(
    async (favouriteId: string, request: string) => {
      addMessage({ role: 'user', content: request });
      setActivity([]);
      setRunning(true);
      setState('ANALYZING');
      await runner.runFavourite(favouriteId, request);
    },
    [addMessage, runner],
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
    setMessages([]);
    setActivity([]);
    setState('IDLE');
    setStep(0);
    setMaxSteps(0);
  }, []);

  return {
    messages,
    activity,
    state,
    step,
    maxSteps,
    running,
    pendingConfirmation,
    send,
    runFavourite,
    stop,
    answerConfirmation,
    clear,
    addMessage,
  };
}
