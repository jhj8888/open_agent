import {
  InMemoryWebStorage,
  UserManager,
  WebStorageStateStore,
} from 'oidc-client-ts';
import { tokenProvider } from './session';

// Tokens live only in this page's memory. Only short-lived PKCE state survives redirect.
export const auth = new UserManager({
  authority: `${window.location.origin}/identity`,
  client_id: 'open-agent-console',
  redirect_uri: `${window.location.origin}/open-agent/callback`,
  response_type: 'code',
  scope: 'openid profile email offline_access',
  userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
  stateStore: new WebStorageStateStore({
    prefix: 'oa.oidc.',
    store: window.sessionStorage,
  }),
  automaticSilentRenew: false,
  loadUserInfo: false,
});

export const accessToken = tokenProvider(auth);

export async function initializeAuth(): Promise<void> {
  await auth.clearStaleState();
  if (window.location.pathname === '/open-agent/callback') {
    try {
      await auth.signinRedirectCallback();
    } finally {
      window.history.replaceState(null, '', '/open-agent/');
    }
  }
}
