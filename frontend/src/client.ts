import createClient, { type Middleware } from 'openapi-fetch';
import type { paths } from './api/schema';
import { UnauthorizedEvent } from './events/UnauthorizedEvent';
import { BACKEND_CONFIG } from './config/constants';
import {
  PROVIDERS,
  modelProvider,
  resolveModelId,
  sameTierModel,
  type ModelProvider,
  type ModelType,
} from './config/models';
import { getAccessToken, getRefreshToken, setTokens, clearTokens } from './services/tokenStorage';
import { useConfigStore } from './stores/configStore';

const getOpenAIApiKey = (): string | null => {
  return localStorage.getItem('openai_api_key');
};

const getAnthropicApiKey = (): string | null => {
  return localStorage.getItem('anthropic_api_key');
};

/** Forget the model API keys held on this device (signing out). */
export const clearApiKeys = (): void => {
  localStorage.removeItem('openai_api_key');
  localStorage.removeItem('anthropic_api_key');
};

/**
 * Whether the API key a provider's models need is usable: held on this
 * device, or held by the server (`GET /config`'s `providers` map) — a
 * browser key always takes precedence when both are present, since the
 * request middleware forwards it ahead of the server falling back to its own.
 */
export const isProviderKeyConfigured = (provider: ModelProvider): boolean => {
  const hasBrowserKey = !!(provider === 'openai' ? getOpenAIApiKey() : getAnthropicApiKey());
  return hasBrowserKey || !!useConfigStore.getState().config?.providers[provider];
};

/**
 * Whether Settings marks a provider's key required: the background features
 * (suggestions, kickstarts, recaps, reports, titles) run on the server's
 * background provider, so its key is needed unless the server holds its own
 * credentials for it. Every other key only unlocks that provider's models.
 * Before /config arrives this is upstream's rule: the OpenAI key is required.
 */
export const isProviderKeyRequired = (provider: ModelProvider): boolean => {
  const config = useConfigStore.getState().config;
  return provider === (config?.backgroundProvider ?? 'openai') && !config?.providers[provider];
};

/**
 * Whether the case view opens Settings on entry. Suggestions, kickstarts,
 * recaps, reports and titles all run on the server's background provider,
 * so the app is unusable until that provider has a key — on this device or
 * the server. (A usable background provider also means some model can
 * answer.) Waits for /config, which names that provider and says whether
 * the server holds its key; without server credentials this is upstream's
 * OpenAI-key gate.
 */
export const shouldAutoOpenSettings = (
  configLoading: boolean,
  backgroundProvider: ModelProvider
): boolean => !configLoading && !isProviderKeyConfigured(backgroundProvider);

/**
 * Whether a provider's models are offered for choosing. When the server
 * holds credentials for some provider, models nothing here can run are
 * withheld: the picker disables them and a stored choice of one falls back.
 * Without server credentials every model is offered, as upstream — a
 * missing key is asked for when a question needs it.
 */
export const isProviderOffered = (provider: ModelProvider): boolean => {
  const serverProviders = useConfigStore.getState().config?.providers;
  const serverHoldsAny = !!serverProviders && PROVIDERS.some(p => serverProviders[p]);
  return !serverHoldsAny || isProviderKeyConfigured(provider);
};

/**
 * Resolves a stored model id (translating retired aliases) to one that's
 * offered (`isProviderOffered`). When its provider isn't, it falls back to
 * the model at the same thinking position on the first offered provider
 * (`sameTierModel`), so a fallback never quietly trades depth for speed —
 * the default GPT-6 Astra medium becomes Claude Fable 5.1 medium, not the
 * fastest Claude. Some provider is always offered: the one the server holds
 * credentials for, or all of them. New questions, the start-up default
 * check and the picker's own auto-correct all resolve through here.
 */
export const resolveUsableModel = (modelId: string | undefined | null): ModelType => {
  const resolved = resolveModelId(modelId);
  if (isProviderOffered(modelProvider(resolved))) return resolved;
  return sameTierModel(resolved, PROVIDERS.find(isProviderOffered)!);
};

export const isWebSearchEnabled = (): boolean => {
  return localStorage.getItem('web_search_enabled') !== 'false';
};

// "Summarize parents": every answer generation also condenses the thread above
// the question into a cheat sheet. Off unless the user turned it on.
export const isParentSummaryEnabled = (): boolean => {
  return localStorage.getItem('summarize_parents') === 'true';
};

export const getVerbosity = (): 'low' | 'medium' => {
  const stored = localStorage.getItem('verbosity');
  return stored === 'normal' ? 'medium' : 'low';
};

const emitUnauthorizedEvent = (nextUrl?: string) => {
  const currentPath = window.location.pathname + window.location.search + window.location.hash;
  const next = nextUrl || encodeURIComponent(window.location.origin + currentPath);
  clearTokens();
  window.dispatchEvent(new UnauthorizedEvent(next));
};

// Track if we're currently refreshing to avoid concurrent refresh calls
let isRefreshing = false;
let refreshPromise: Promise<boolean> | null = null;

const tryRefreshToken = async (): Promise<boolean> => {
  if (isRefreshing && refreshPromise) {
    return refreshPromise;
  }

  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    return false;
  }

  isRefreshing = true;
  refreshPromise = fetch(`${BACKEND_CONFIG.BACKEND_HTTP_URL}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  })
    .then(async response => {
      if (response.ok) {
        const data = await response.json();
        setTokens(data.access_token, data.refresh_token);
        return true;
      }
      return false;
    })
    .finally(() => {
      isRefreshing = false;
      refreshPromise = null;
    });

  return refreshPromise;
};

const apiKeyMiddleware: Middleware = {
  async onRequest({ request }) {
    const openaiKey = getOpenAIApiKey();
    if (openaiKey) {
      request.headers.set('X-OpenAI-API-Key', openaiKey);
    }
    const anthropicKey = getAnthropicApiKey();
    if (anthropicKey) {
      request.headers.set('X-Anthropic-API-Key', anthropicKey);
    }
    return request;
  },
};

const bearerTokenMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = getAccessToken();
    if (token) {
      request.headers.set('Authorization', `Bearer ${token}`);
    }
    return request;
  },
};

const retryClones = new WeakMap<Request, Request>();

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    retryClones.set(request, request.clone());
    return request;
  },
  async onResponse({ request, response }) {
    const url = new URL(request.url);
    // Token-minting endpoints must never trigger a refresh attempt themselves.
    const isTokenEndpoint =
      url.pathname.endsWith('/auth/refresh') || url.pathname.endsWith('/auth/token');
    // /auth/me is the startup auth check: refresh applies (an expired access
    // token with a live refresh token must not log the user out), but a failed
    // refresh returns the 401 untouched — AuthContext turns it into the login
    // redirect, and emitting here would re-trigger fetchUser via AuthEventHandler.
    const isAuthCheck = url.pathname.endsWith('/auth/me');

    if (response.status === 401 && !isTokenEndpoint) {
      const refreshed = await tryRefreshToken();

      if (refreshed) {
        const clone = retryClones.get(request);
        if (clone) {
          retryClones.delete(request);
          // Set the new Bearer token on the retried request
          const token = getAccessToken();
          if (token) {
            clone.headers.set('Authorization', `Bearer ${token}`);
          }
          return await fetch(clone);
        }
      } else if (!isAuthCheck) {
        emitUnauthorizedEvent();
      }
    }

    retryClones.delete(request);
    return response;
  },
};

const client = createClient<paths>({
  baseUrl: BACKEND_CONFIG.BACKEND_HTTP_URL,
});
client.use(apiKeyMiddleware);
client.use(bearerTokenMiddleware);
client.use(authMiddleware);

/**
 * URL of the per-graph generation-job event stream.
 *
 * A WebSocket handshake cannot carry an Authorization header, so whatever
 * authenticates the stream sits in the URL — where servers and proxies log it.
 * What goes there is therefore a ticket, not the session token: this request
 * authenticates normally (token in a header) and gets back a credential that
 * opens this one case's stream, expires within seconds, and grants nothing else.
 *
 * Returns null when there is no stream left to open: the case is gone, or the
 * session ended and the app is on its way to the login screen. Every other
 * failure throws, and the caller retries.
 */
const createJobStreamUrl = async (graphId: string): Promise<string | null> => {
  const { data, error, response } = await client.POST('/research/{graph_id}/stream-ticket', {
    params: { path: { graph_id: graphId } },
  });
  if (response.status === 404 || response.status === 401) return null;
  if (error || !data) throw new Error('Could not authorize the live answer stream.');
  const base = `${BACKEND_CONFIG.BACKEND_WS_URL}/research/${graphId}/stream`;
  return `${base}?ticket=${encodeURIComponent(data.ticket)}`;
};

export { client, createJobStreamUrl };
