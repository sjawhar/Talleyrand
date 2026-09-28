import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { fetchAppConfig, type AppConfig } from '../services/configService';

interface ConfigStore {
  config: AppConfig | null;
  // True until the first fetch settles (success or failure) — gates the
  // login decision so it never renders against a guessed default.
  isLoading: boolean;
  fetchConfig: () => Promise<void>;
}

// Module-level guard: React StrictMode double-invokes effects, and the
// router's config fetch is triggered from an effect too — without this, both
// would race and fire the request twice.
let fetchPromise: Promise<void> | null = null;

export const useConfigStore = create<ConfigStore>()(
  devtools(set => ({
    config: null,
    isLoading: true,
    fetchConfig: async () => {
      if (fetchPromise) return fetchPromise;
      fetchPromise = (async () => {
        try {
          const config = await fetchAppConfig();
          set({ config, isLoading: false }, false, 'configStore/fetchConfig/success');
        } catch (err) {
          console.error('Failed to fetch app config:', err);
          set({ isLoading: false }, false, 'configStore/fetchConfig/error');
        }
      })();
      await fetchPromise;
    },
  }))
);
