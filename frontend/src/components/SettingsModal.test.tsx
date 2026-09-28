import type { ReactNode } from 'react';
import type * as ReactDom from 'react-dom';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { ModelProvider } from '../config/models';
import type { AppConfig } from '../services/configService';
import { useConfigStore } from '../stores/configStore';
import { SettingsModal } from './SettingsModal';

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null, isLoading: false, isAuthenticated: true, refetch: async () => {} }),
}));
// Sign-out only; its module reads window.location when it loads.
vi.mock('../services/authService', () => ({ AuthService: {} }));
// The modal portals into document.body; render it in place instead.
vi.mock('react-dom', async importOriginal => {
  const actual = await importOriginal<typeof ReactDom & { default: typeof ReactDom }>();
  const createPortal = (children: ReactNode) => children;
  return { ...actual, createPortal, default: { ...actual.default, createPortal } };
});

// Vitest runs in node: give the modules the browser globals they read. The
// research store reads localStorage as it loads; the modal names
// document.body as its portal target.
vi.hoisted(() => {
  const items: Record<string, string> = {};
  globalThis.localStorage = {
    getItem: (key: string) => items[key] ?? null,
    setItem: (key: string, value: string) => void (items[key] = value),
    removeItem: (key: string) => void delete items[key],
    clear: () => Object.keys(items).forEach(key => delete items[key]),
    key: () => null,
    length: 0,
  };
  globalThis.document = { body: {} } as Document;
});

const serverConfig = (
  providers: Partial<Record<ModelProvider, boolean>>,
  backgroundProvider: ModelProvider
): void => {
  const config: AppConfig = {
    localLogin: false,
    localUserEmail: null,
    providers: { openai: false, anthropic: false, ...providers },
    backgroundProvider,
    dictation: true,
  };
  useConfigStore.setState({ config, isLoading: false });
};

/** The rendered OpenAI key field: from its label up to the Anthropic key field. */
const openAiKeyField = (): string => {
  const html = renderToStaticMarkup(<SettingsModal isOpen onClose={() => {}} onSave={() => {}} />);
  const start = html.indexOf('OpenAI API key');
  const end = html.indexOf('Anthropic API key');
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);
  return html.slice(start, end);
};

describe('Settings: the OpenAI key field', () => {
  it('is not required when the background features run on server-held Claude credentials', () => {
    serverConfig({ anthropic: true }, 'anthropic');
    const field = openAiKeyField();
    // Nothing on this instance needs an OpenAI key: answers can use Claude and
    // every background feature runs on Claude through the server's credentials.
    expect(field).not.toMatch(/>Required</);
    expect(field).not.toMatch(/doesn(&#x27;|')t work without it/);
  });

  it('stays required as upstream when the server holds no credentials', () => {
    serverConfig({}, 'openai');
    expect(openAiKeyField()).toMatch(/>Required</);
  });
});
