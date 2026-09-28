import { BACKEND_CONFIG } from '../config/constants';
import type { components } from '../api/schema';

export type AppConfig = components['schemas']['AppConfigResponse'];

/**
 * Runtime configuration the frontend reads before offering sign-in options —
 * whether the server holds local-login and model-provider credentials, which
 * provider runs background features, and whether dictation is available.
 *
 * Uses a raw fetch rather than the typed `client`, matching the other
 * pre-auth calls in authService/AuthContext: this runs before the app knows
 * whether a session exists, so it must not depend on the client's bearer or
 * API-key middleware.
 */
export async function fetchAppConfig(): Promise<AppConfig> {
  const response = await fetch(`${BACKEND_CONFIG.BACKEND_HTTP_URL}/config`);
  if (!response.ok) {
    throw new Error(`Failed to fetch app config: ${response.status}`);
  }
  return response.json();
}
