import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ErrorResponse, User } from 'oidc-client-ts';
import { tokenProvider, sessionExpiredMessage } from './session.js';

const user = (seconds: number) =>
  new User({
    access_token: 'synthetic-access',
    refresh_token: 'synthetic-refresh',
    token_type: 'Bearer',
    expires_at: Math.floor(Date.now() / 1000) + seconds,
    profile: {
      sub: 'fixture',
      iss: 'https://identity.invalid',
      aud: 'console',
      exp: 0,
      iat: 0,
    },
  });

test('valid sessions do not refresh', async () => {
  const token = tokenProvider({
    getUser: async () => user(120),
    signinSilent: async () => {
      throw new Error('unexpected refresh');
    },
    removeUser: async () => {
      throw new Error('unexpected remove');
    },
  });
  assert.equal(await token(), 'synthetic-access');
});

test('concurrent callers share one SDK refresh', async () => {
  let calls = 0;
  const token = tokenProvider({
    getUser: async () => user(10),
    signinSilent: async () => {
      calls++;
      await new Promise(resolve => setTimeout(resolve, 5));
      return user(120);
    },
    removeUser: async () => {
      throw new Error('unexpected remove');
    },
  });
  assert.deepEqual(
    await Promise.all([token(), token(), token()]),
    Array(3).fill('synthetic-access')
  );
  assert.equal(calls, 1);
});

test('expired refresh removes identity once and never echoes provider details', async () => {
  let removed = 0;
  const token = tokenProvider({
    getUser: async () => user(10),
    signinSilent: async () => {
      throw new ErrorResponse({
        error: 'invalid_grant',
        error_description: 'private-provider-detail',
      });
    },
    removeUser: async () => {
      removed++;
    },
  });
  const results = await Promise.allSettled([token(), token()]);
  for (const result of results) {
    assert.equal(result.status, 'rejected');
    if (result.status === 'rejected')
      assert.equal(result.reason.message, sessionExpiredMessage);
  }
  assert.equal(removed, 1);
});

test('temporary network failure retains identity and permits a later refresh', async () => {
  let calls = 0;
  const token = tokenProvider({
    getUser: async () => user(10),
    signinSilent: async () => {
      if (++calls === 1) throw new TypeError('private-url');
      return user(120);
    },
    removeUser: async () => {
      throw new Error('must retain session');
    },
  });
  await assert.rejects(token(), {
    message: '身份服务暂时不可用，请稍后重试。',
  });
  assert.equal(await token(), 'synthetic-access');
});

test('expired access without refresh clears the session', async () => {
  const expired = user(-1);
  expired.refresh_token = undefined;
  let removed = false;
  const token = tokenProvider({
    getUser: async () => expired,
    signinSilent: async () => {
      throw new Error('unexpected');
    },
    removeUser: async () => {
      removed = true;
    },
  });
  await assert.rejects(token(), { message: sessionExpiredMessage });
  assert.equal(removed, true);
});
