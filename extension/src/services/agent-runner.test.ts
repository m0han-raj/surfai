/**
 * Agent runner: the client half of the loop (AC-07, AC-14, AC-15).
 *
 * The API and page bridge are mocked, so these tests assert control flow --
 * that the page is re-observed between steps, that confirmation actually
 * suspends the loop, and that Stop prevents further actions.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Directive } from '../types/agent';

const apiMock = {
  chat: vi.fn(),
  createTask: vi.fn(),
  continueTask: vi.fn(),
  cancelTask: vi.fn(),
  getFavourite: vi.fn(),
};

const messagingMock = {
  capturePage: vi.fn(),
  executeAction: vi.fn(),
  getTabContext: vi.fn(),
  openUrl: vi.fn(),
  waitForTabLoad: vi.fn(),
};

vi.mock('./api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {
    isNetwork = false;
  },
}));

vi.mock('./messaging', () => messagingMock);

const { AgentRunner } = await import('./agent-runner');

function directive(overrides: Partial<Directive>): Directive {
  return {
    type: 'action',
    task_id: 't1',
    state: 'EXECUTING',
    activity: 'Working',
    step: 1,
    max_steps: 15,
    action: null,
    risk: null,
    message: '',
    warnings: [],
    ...overrides,
  };
}

function page(title = 'Page one') {
  return {
    url: 'https://shop.example.com/products',
    domain: 'shop.example.com',
    title,
    summary: '',
    elements: [],
    truncated: 0,
    capturedAt: Date.now(),
  };
}

function callbacks() {
  return {
    onDirective: vi.fn(),
    onActivity: vi.fn(),
    onConfirmationRequired: vi.fn().mockResolvedValue(true),
    onFinished: vi.fn(),
    onError: vi.fn(),
  };
}

describe('AgentRunner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    messagingMock.getTabContext.mockResolvedValue({
      ok: true,
      data: { url: 'https://shop.example.com/products', title: 'Products' },
    });
    messagingMock.capturePage.mockResolvedValue({ ok: true, data: page() });
    messagingMock.executeAction.mockResolvedValue({
      ok: true,
      data: {
        success: true,
        action: 'CLICK',
        target: 'e2',
        url_changed: false,
        page_changed: true,
      },
    });
    messagingMock.openUrl.mockResolvedValue({ ok: true });
    messagingMock.waitForTabLoad.mockResolvedValue(true);
    apiMock.cancelTask.mockResolvedValue(directive({ type: 'answer', state: 'CANCELLED' }));
  });

  it('runs actions until the backend answers', async () => {
    apiMock.chat.mockResolvedValue(
      directive({ action: { action: 'TYPE', target: 'e1', value: 'laptop' } }),
    );
    apiMock.continueTask
      .mockResolvedValueOnce(directive({ action: { action: 'CLICK', target: 'e2' } }))
      .mockResolvedValueOnce(
        directive({ type: 'answer', state: 'COMPLETED', message: 'Found 3 laptops.' }),
      );

    const handlers = callbacks();
    await new AgentRunner(handlers).send('find laptops');

    expect(messagingMock.executeAction).toHaveBeenCalledTimes(2);
    expect(handlers.onFinished).toHaveBeenCalledWith(
      expect.objectContaining({ state: 'COMPLETED', message: 'Found 3 laptops.' }),
    );
    expect(handlers.onError).not.toHaveBeenCalled();
  });

  it('re-observes the page after every action', async () => {
    apiMock.chat.mockResolvedValue(directive({ action: { action: 'CLICK', target: 'e2' } }));
    apiMock.continueTask.mockResolvedValue(
      directive({ type: 'answer', state: 'COMPLETED', message: 'done' }),
    );

    messagingMock.capturePage
      .mockResolvedValueOnce({ ok: true, data: page('Page one') })
      .mockResolvedValueOnce({ ok: true, data: page('Page two') });

    await new AgentRunner(callbacks()).send('search');

    // The second snapshot -- taken after the action -- is what was sent back.
    const [, payload] = apiMock.continueTask.mock.calls[0];
    expect(payload.page_context.title).toBe('Page two');
    expect(payload.result.success).toBe(true);
  });

  it('waits for the page to load before re-observing after a navigation', async () => {
    apiMock.chat.mockResolvedValue(
      directive({ action: { action: 'NAVIGATE', value: 'https://shop.example.com/deals' } }),
    );
    apiMock.continueTask.mockResolvedValue(
      directive({ type: 'answer', state: 'COMPLETED', message: 'done' }),
    );
    messagingMock.executeAction.mockResolvedValue({
      ok: true,
      data: {
        success: true,
        action: 'NAVIGATE',
        target: null,
        url_changed: true,
        page_changed: true,
      },
    });

    await new AgentRunner(callbacks()).send('open deals');
    expect(messagingMock.waitForTabLoad).toHaveBeenCalled();
  });

  // --- confirmation (AC-14) ---------------------------------------------

  it('suspends on a confirm directive and resumes when approved', async () => {
    apiMock.chat.mockResolvedValue(
      directive({
        type: 'confirm',
        state: 'WAITING_CONFIRMATION',
        action: { action: 'CLICK', target: 'e6' },
        risk: {
          level: 'HIGH',
          requires_confirmation: true,
          category: 'PURCHASE',
          explanation: 'This may complete a purchase.',
        },
      }),
    );
    apiMock.continueTask.mockResolvedValue(
      directive({ type: 'answer', state: 'COMPLETED', message: 'Order placed.' }),
    );

    const handlers = callbacks();
    await new AgentRunner(handlers).send('buy it');

    expect(handlers.onConfirmationRequired).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'confirm' }),
    );
    expect(apiMock.continueTask).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ confirmation: true }),
    );
  });

  it('sends a decline when the user cancels', async () => {
    apiMock.chat.mockResolvedValue(
      directive({ type: 'confirm', state: 'WAITING_CONFIRMATION' }),
    );
    apiMock.continueTask.mockResolvedValue(
      directive({ type: 'error', state: 'CANCELLED', message: 'Action declined.' }),
    );

    const handlers = callbacks();
    handlers.onConfirmationRequired.mockResolvedValue(false);
    await new AgentRunner(handlers).send('buy it');

    expect(apiMock.continueTask).toHaveBeenCalledWith(
      't1',
      expect.objectContaining({ confirmation: false }),
    );
    // Nothing was executed in the page.
    expect(messagingMock.executeAction).not.toHaveBeenCalled();
  });

  // --- stop (AC-15) ------------------------------------------------------

  it('stop prevents any further action', async () => {
    const handlers = callbacks();
    const runner = new AgentRunner(handlers);

    apiMock.chat.mockResolvedValue(directive({ action: { action: 'CLICK', target: 'e2' } }));
    apiMock.continueTask.mockImplementation(async () => {
      // The user hits Stop while the backend is deciding the next step.
      await runner.stop();
      return directive({ action: { action: 'CLICK', target: 'e3' } });
    });

    await runner.send('search');

    expect(messagingMock.executeAction).toHaveBeenCalledTimes(1);
    expect(apiMock.cancelTask).toHaveBeenCalledWith('t1');
    expect(handlers.onFinished).toHaveBeenCalledWith(
      expect.objectContaining({ state: 'CANCELLED' }),
    );
  });

  it('stop still stops locally when the backend cancel call fails', async () => {
    apiMock.cancelTask.mockRejectedValue(new Error('backend down'));
    const runner = new AgentRunner(callbacks());
    await expect(runner.stop()).resolves.toBeUndefined();
    expect(runner.isRunning).toBe(false);
  });

  it('refuses to start a second task while one is running', async () => {
    let release: (value: Directive) => void = () => undefined;
    apiMock.chat.mockReturnValue(
      new Promise<Directive>((resolve) => {
        release = resolve;
      }),
    );

    const handlers = callbacks();
    const runner = new AgentRunner(handlers);
    const first = runner.send('one');
    await runner.send('two');

    expect(handlers.onError).toHaveBeenCalledWith(expect.stringContaining('already running'));
    release(directive({ type: 'answer', state: 'COMPLETED', message: 'done' }));
    await first;
  });

  // --- failures ----------------------------------------------------------

  it('reports a failed action to the backend rather than stopping', async () => {
    apiMock.chat.mockResolvedValue(directive({ action: { action: 'CLICK', target: 'e2' } }));
    messagingMock.executeAction.mockResolvedValue({
      ok: false,
      error: 'Element no longer exists',
    });
    apiMock.continueTask.mockResolvedValue(
      directive({ type: 'answer', state: 'COMPLETED', message: 'recovered' }),
    );

    await new AgentRunner(callbacks()).send('search');

    const [, payload] = apiMock.continueTask.mock.calls[0];
    expect(payload.result.success).toBe(false);
    expect(payload.result.error).toBe('Element no longer exists');
  });

  it('surfaces a backend failure as an error message', async () => {
    apiMock.chat.mockRejectedValue(new Error('Could not reach the SurfAI backend.'));

    const handlers = callbacks();
    await new AgentRunner(handlers).send('search');

    expect(handlers.onError).toHaveBeenCalledWith(
      expect.stringContaining('Could not reach the SurfAI backend.'),
    );
  });

  it('still runs when the page cannot be read, and says so', async () => {
    messagingMock.capturePage.mockResolvedValue({
      ok: false,
      error: 'SurfAI cannot read this page.',
    });
    apiMock.chat.mockResolvedValue(
      directive({ type: 'answer', state: 'COMPLETED', message: 'I cannot see this page.' }),
    );

    const handlers = callbacks();
    await new AgentRunner(handlers).send('what is here?');

    expect(handlers.onActivity).toHaveBeenCalledWith(
      'SurfAI cannot read this page.',
      'info',
    );
    const payload = apiMock.chat.mock.calls[0][0];
    expect(payload.page_context.url).toBe('https://shop.example.com/products');
  });

  // --- favourites (AC-12) ------------------------------------------------

  it('navigates to a favourite before starting its task', async () => {
    apiMock.getFavourite.mockResolvedValue({
      id: 'f1',
      name: 'AI Jobs',
      url: 'https://jobs.example.com/search',
    });
    apiMock.createTask.mockResolvedValue(
      directive({ type: 'answer', state: 'COMPLETED', message: '4 new roles.' }),
    );

    const handlers = callbacks();
    await new AgentRunner(handlers).runFavourite('f1', 'check my AI jobs');

    expect(messagingMock.openUrl).toHaveBeenCalledWith('https://jobs.example.com/search');
    expect(messagingMock.waitForTabLoad).toHaveBeenCalled();
    expect(apiMock.createTask).toHaveBeenCalledWith(
      expect.objectContaining({ favourite_id: 'f1', request: 'check my AI jobs' }),
    );
  });

  it('follows favourite_navigation returned by chat', async () => {
    apiMock.chat.mockResolvedValue(
      directive({
        type: 'answer',
        state: 'COMPLETED',
        message: 'opened',
        favourite_navigation: 'https://jobs.example.com/search',
      }),
    );

    await new AgentRunner(callbacks()).send('check my AI jobs');
    expect(messagingMock.openUrl).toHaveBeenCalledWith('https://jobs.example.com/search');
  });
});
