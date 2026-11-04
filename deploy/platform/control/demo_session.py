"""Authenticated demo verification helpers; no token persistence or bootstrap grants."""

import base64
import hashlib
import http.cookiejar
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from provision_data import ROOT, container_address


class DemoSession:
    def __init__(self, username='demo', password=None, require_tenant=True):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                 urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.api = 'http://127.0.0.1:18082'
        identity = json.loads((ROOT / 'identity-secrets.json').read_text())
        metadata = json.loads((ROOT / 'identity-deployment.json').read_text())
        endpoint = 'http://' + container_address('open-agent-control-identity') + ':8000'
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        query = urllib.parse.urlencode({'clientId': metadata['client_id'], 'responseType': 'code',
                                       'redirectUri': 'http://127.0.0.1:18080/callback', 'scope': 'openid profile',
                                       'state': secrets.token_urlsafe(24), 'nonce': secrets.token_urlsafe(24),
                                       'code_challenge_method': 'S256', 'code_challenge': challenge})
        login = self.request(endpoint + '/api/login?' + query, {
            'application': 'open-agent', 'organization': 'open-agent', 'username': username,
            'password': password if password is not None else identity['demo_password'], 'type': 'code', 'signinMethod': 'Password',
        })
        if login.get('status') != 'ok' or not isinstance(login.get('data'), str):
            raise RuntimeError('Demo authorization failed')
        tokens = self.request(endpoint + '/api/login/oauth/access_token', {
            'grant_type': 'authorization_code', 'client_id': metadata['client_id'], 'code': login['data'],
            'code_verifier': verifier, 'redirect_uri': 'http://127.0.0.1:18080/callback',
        })
        self.auth = {'Authorization': 'Bearer ' + tokens['access_token']}
        tenants = self.call('/platform/tenants')
        # Subject is inspected only after the platform's JWT verifier accepted it.
        self.uid = json.loads(base64.urlsafe_b64decode(tokens['access_token'].split('.')[1] + '==='))['sub']
        if not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', self.uid):
            raise RuntimeError('Unexpected verified subject')
        if not require_tenant:
            return
        owned = [tenant for tenant in tenants['tenants'] if tenant['displayName'] == '演示租户']
        if len(owned) != 1:
            raise RuntimeError('Demo tenant is unavailable or ambiguous')
        self.tenant = owned[0]['tenantId']
        self.management = '/platform/tenants/' + self.tenant + '/environments/test'

    def request(self, url, body=None, headers=None, method=None):
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         headers={'Accept': 'application/json', 'Content-Type': 'application/json', **(headers or {})}, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Never print response/input: it may contain credentials or user content.
            raise RuntimeError('Demo API response status=' + str(error.code)) from None

    def call(self, route, body=None, headers=None, method=None):
        result = self.request(self.api + route, body, {**self.auth, **(headers or {})}, method)
        if result.get('code') != 0:
            raise RuntimeError('Demo API operation failed')
        return result.get('data')
