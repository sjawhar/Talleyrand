import { clearApiKeys } from '../client';
import { BACKEND_CONFIG } from '../config/constants';
import { researchGenerationService } from './researchGenerationService';
import { clearTokens, setTokens } from './tokenStorage';

const RESEARCH_URL = window.location.origin + '/research';

export class AuthService {
  static async initiateGoogleLogin(): Promise<void> {
    // Get next_url from URL params if available (for redirect after 401)
    const urlParams = new URLSearchParams(window.location.search);
    const nextUrl = urlParams.get('next_url') || RESEARCH_URL;

    // Navigate directly to the backend endpoint, which sets the oauth_state
    // cookie (first-party on the backend domain) and redirects to Google.
    const redirectUrl = `${BACKEND_CONFIG.BACKEND_HTTP_URL}/oauth/google/redirect_url?next_url=${encodeURIComponent(nextUrl)}`;
    window.location.href = redirectUrl;
  }

  /**
   * Issue tokens for the single local user this instance is configured for,
   * bypassing Google entirely. Only ever called when `GET /config` reports
   * `localLogin: true` — otherwise the backend route doesn't exist.
   */
  static async localLogin(): Promise<void> {
    const response = await fetch(`${BACKEND_CONFIG.BACKEND_HTTP_URL}/auth/local-login`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error(`Local login failed: ${response.status}`);
    }
    const data = await response.json();
    setTokens(data.access_token, data.refresh_token);
  }

  /**
   * End the session on this device: detach the live answer stream, forget the
   * session tokens and the model API keys, then leave for the login page by a
   * full page load, so no case content survives in memory for whoever sits
   * down next.
   *
   * This forgets the tokens; it cannot revoke them. The server has no
   * revocation, so a token already copied elsewhere stays usable until it
   * expires.
   */
  static signOut(): void {
    researchGenerationService.close();
    clearTokens();
    clearApiKeys();
    window.location.assign('/login');
  }
}
