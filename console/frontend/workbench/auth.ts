import {
  InMemoryWebStorage,
  UserManager,
  WebStorageStateStore,
} from 'oidc-client-ts';

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

let refreshing: ReturnType<typeof auth.signinSilent> | undefined;
export async function accessToken(): Promise<string> {
  let user = await auth.getUser();
  if (user && (user.expires_in ?? 0) < 30 && user.refresh_token) {
    refreshing ??= auth.signinSilent().finally(() => {
      refreshing = undefined;
    });
    user = await refreshing;
  }
  if (!user || user.expired) throw new Error('登录已过期，请重新登录。');
  return user.access_token;
}

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
