/**
 * Keeps the panel's view of the current page fresh.
 *
 * Re-observes whenever the user switches tabs or navigates, so the Current Page
 * card never describes a page the user has already left. Analysis runs on the
 * backend's `/observe` endpoint, which is pure heuristics -- no model call, so
 * refreshing is effectively free.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../services/api';
import { capturePage, getTabContext, onTabChanged } from '../services/messaging';
import type { TabContext } from '../types/messages';

export interface PageInsight {
  url: string;
  domain: string;
  title: string;
  pageType: string;
  capabilities: Record<string, boolean>;
  elementCount: number;
  suspicious: boolean;
  tools: string[];
}

export function usePageContext() {
  const [insight, setInsight] = useState<PageInsight | null>(null);
  const [tab, setTab] = useState<TabContext>({ url: '', title: '' });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Guards against a slow response for a page the user has already left.
  const requestId = useRef(0);

  const refresh = useCallback(async () => {
    const id = ++requestId.current;
    setLoading(true);
    setError(null);

    const tabResponse = await getTabContext();
    if (id !== requestId.current) return;

    if (tabResponse.data) setTab(tabResponse.data);

    if (!tabResponse.ok || tabResponse.error) {
      setError(tabResponse.error ?? 'SurfAI could not read the current tab.');
      setInsight(null);
      setLoading(false);
      return;
    }

    const pageResponse = await capturePage();
    if (id !== requestId.current) return;

    if (!pageResponse.ok || !pageResponse.data) {
      setError(pageResponse.error ?? 'SurfAI could not read this page.');
      setInsight(null);
      setLoading(false);
      return;
    }

    try {
      const analysis = await api.observe(pageResponse.data as unknown as Record<string, unknown>);
      if (id !== requestId.current) return;

      setInsight({
        url: analysis.page.url,
        domain: analysis.page.domain,
        title: analysis.page.title,
        pageType: analysis.page.page_type,
        capabilities: analysis.page.capabilities,
        elementCount: analysis.element_count,
        suspicious: analysis.security.is_suspicious,
        tools: analysis.tools.map((tool) => tool.name),
      });
      setError(null);
    } catch (err) {
      if (id !== requestId.current) return;
      // The page was still read successfully; show what we have and explain
      // that the backend analysis is missing rather than blanking the card.
      const page = pageResponse.data;
      setInsight({
        url: page.url,
        domain: page.domain,
        title: page.title,
        pageType: 'unknown',
        capabilities: {},
        elementCount: page.elements.length,
        suspicious: false,
        tools: [],
      });
      setError(
        err instanceof ApiError && err.isNetwork
          ? 'Backend not reachable. Start it with `docker compose up`, or check Settings.'
          : 'SurfAI could not analyse this page.',
      );
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return onTabChanged(() => {
      void refresh();
    });
  }, [refresh]);

  return { insight, tab, loading, error, refresh };
}
