import { ErrorResponse } from 'oidc-client-ts';
import type { UserManager } from 'oidc-client-ts';

export const sessionExpiredMessage =
  '登录已过期，请重新登录。未提交的操作不会自动重试。';

// Wrap the official SDK, keeping refresh single-flight and error bodies private.
export function tokenProvider(
  manager: Pick<UserManager, 'getUser' | 'signinSilent' | 'removeUser'>
): () => Promise<string> {
  let refreshing: ReturnType<UserManager['signinSilent']> | undefined;
  const expired = async (): Promise<never> => {
    await manager.removeUser();
    throw new Error(sessionExpiredMessage);
  };
  return async () => {
    let user = await manager.getUser();
    if (user && (user.expires_in ?? 0) < 30 && user.refresh_token) {
      refreshing ??= manager
        .signinSilent()
        .catch(async (error: unknown) => {
          if (
            error instanceof ErrorResponse &&
            [
              'invalid_grant',
              'login_required',
              'interaction_required',
              'consent_required',
              'invalid_token',
            ].includes(error.error ?? '')
          )
            return expired();
          throw new Error('身份服务暂时不可用，请稍后重试。');
        })
        .finally(() => {
          refreshing = undefined;
        });
      user = await refreshing;
    }
    if (!user || user.expired) return expired();
    return user.access_token;
  };
}
