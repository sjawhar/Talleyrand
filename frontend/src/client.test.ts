import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  isProviderKeyConfigured,
  isProviderKeyRequired,
  isProviderOffered,
  resolveUsableModel,
  shouldAutoOpenSettings,
} from './client';
import { DEFAULT_MODEL, type ModelProvider } from './config/models';
import type { AppConfig } from './services/configService';
import { useConfigStore } from './stores/configStore';

class MemoryStorage {
  private items = new Map<string, string>();
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
  clear(): void {
    this.items.clear();
  }
}

/** `/config` as the server answers it: which providers it holds credentials for. */
const serverConfig = (
  providers: Partial<Record<ModelProvider, boolean>>,
  backgroundProvider: ModelProvider = 'openai'
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

const browserKey = (provider: ModelProvider): void => {
  localStorage.setItem(provider === 'openai' ? 'openai_api_key' : 'anthropic_api_key', 'sk-test');
};

beforeEach(() => {
  vi.stubGlobal('localStorage', new MemoryStorage());
  useConfigStore.setState({ config: null, isLoading: true });
});

describe('isProviderKeyConfigured', () => {
  it('without server credentials, counts only keys held on this device', () => {
    serverConfig({});
    expect(isProviderKeyConfigured('openai')).toBe(false);
    expect(isProviderKeyConfigured('anthropic')).toBe(false);

    browserKey('openai');
    expect(isProviderKeyConfigured('openai')).toBe(true);
    expect(isProviderKeyConfigured('anthropic')).toBe(false);
  });

  it('counts the server holding credentials for that provider', () => {
    serverConfig({ anthropic: true });
    expect(isProviderKeyConfigured('anthropic')).toBe(true);
    expect(isProviderKeyConfigured('openai')).toBe(false);
  });

  it('counts browser keys when /config never arrived', () => {
    useConfigStore.setState({ config: null, isLoading: false });
    expect(isProviderKeyConfigured('anthropic')).toBe(false);
    browserKey('anthropic');
    expect(isProviderKeyConfigured('anthropic')).toBe(true);
  });
});

describe('isProviderOffered', () => {
  it('without server credentials, offers every provider even with no keys', () => {
    serverConfig({});
    expect(isProviderOffered('openai')).toBe(true);
    expect(isProviderOffered('anthropic')).toBe(true);
  });

  it('offers every provider when /config never arrived', () => {
    useConfigStore.setState({ config: null, isLoading: false });
    expect(isProviderOffered('openai')).toBe(true);
    expect(isProviderOffered('anthropic')).toBe(true);
  });

  it('with server credentials, withholds a provider with no key anywhere', () => {
    serverConfig({ anthropic: true });
    expect(isProviderOffered('anthropic')).toBe(true);
    expect(isProviderOffered('openai')).toBe(false);
  });

  it('with server credentials, offers a provider again once this device holds its key', () => {
    serverConfig({ anthropic: true });
    browserKey('openai');
    expect(isProviderOffered('openai')).toBe(true);
  });
});

describe('resolveUsableModel', () => {
  it('without server credentials, keeps the stored model whether or not a key is held', () => {
    serverConfig({});
    expect(resolveUsableModel('claude-fable-5-1-high')).toBe('claude-fable-5-1-high');
    expect(resolveUsableModel('gpt-6-astra-high')).toBe('gpt-6-astra-high');
    expect(resolveUsableModel(null)).toBe(DEFAULT_MODEL);
  });

  it.each([
    [null, 'claude-fable-5-1-medium'],
    ['gpt-6-astra-medium', 'claude-fable-5-1-medium'],
    ['gpt-6-astra-high', 'claude-fable-5-1-high'],
    ['gpt-6-astra-max', 'claude-fable-5-1-max'],
    ['gpt-5.6-luna', 'claude-haiku-4-5'],
    // A retired id is translated first, then moved at its replacement's depth
    ['gpt-5.6-sol-high', 'claude-fable-5-1-high'],
  ] as const)(
    'with only Claude on the server, moves %s to %s at the same depth',
    (stored, expected) => {
      serverConfig({ anthropic: true });
      expect(resolveUsableModel(stored)).toBe(expected);
    }
  );

  it('with only OpenAI on the server, moves a Claude choice to GPT at the same depth', () => {
    serverConfig({ openai: true });
    expect(resolveUsableModel('claude-fable-5-1-high')).toBe('gpt-6-astra-high');
  });

  it('keeps a model whose provider the server holds', () => {
    serverConfig({ anthropic: true });
    expect(resolveUsableModel('claude-sonnet-5')).toBe('claude-sonnet-5');
  });

  it('keeps a model whose key this device holds, even when the server holds another provider', () => {
    serverConfig({ anthropic: true });
    browserKey('openai');
    expect(resolveUsableModel('gpt-6-astra-high')).toBe('gpt-6-astra-high');
  });
});

describe('shouldAutoOpenSettings', () => {
  it('waits for /config before deciding', () => {
    expect(shouldAutoOpenSettings(true, 'openai')).toBe(false);
  });

  it('without server credentials, opens until an OpenAI key is held (as upstream)', () => {
    serverConfig({});
    expect(shouldAutoOpenSettings(false, 'openai')).toBe(true);
    browserKey('openai');
    expect(shouldAutoOpenSettings(false, 'openai')).toBe(false);
  });

  it('stays closed when the server holds the background provider', () => {
    serverConfig({ anthropic: true }, 'anthropic');
    expect(shouldAutoOpenSettings(false, 'anthropic')).toBe(false);
  });

  it('opens when the server holds only a provider other than the background one', () => {
    serverConfig({ anthropic: true }, 'openai');
    expect(shouldAutoOpenSettings(false, 'openai')).toBe(true);
  });
});

describe('isProviderKeyRequired', () => {
  it.each([
    // [server holds, background provider, OpenAI required, Anthropic required]
    [{}, 'openai', true, false],
    [{}, 'anthropic', false, true],
    [{ anthropic: true }, 'anthropic', false, false],
    [{ anthropic: true }, 'openai', true, false],
  ] as const)(
    'with server credentials %o and background provider %s: OpenAI %s, Anthropic %s',
    (providers, background, openaiRequired, anthropicRequired) => {
      serverConfig(providers, background);
      expect(isProviderKeyRequired('openai')).toBe(openaiRequired);
      expect(isProviderKeyRequired('anthropic')).toBe(anthropicRequired);
    }
  );

  it('requires the OpenAI key, as upstream, when /config never arrived', () => {
    useConfigStore.setState({ config: null, isLoading: false });
    expect(isProviderKeyRequired('openai')).toBe(true);
    expect(isProviderKeyRequired('anthropic')).toBe(false);
  });
});
