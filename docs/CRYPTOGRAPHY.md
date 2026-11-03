# Cryptographic mechanisms and deployment limits

This page describes the implementation and its configuration boundaries. It does
not establish the security of operator-supplied credentials, imported keys, or
external services.

## Built-in mechanisms

| Purpose | Implementation |
| --- | --- |
| Tenant API keys and secrets | `core/tenant/tools/generator/app.go` reads `crypto/rand.Reader`; keys use 16 random bytes and secrets use 24 random bytes. Random-source failure stops generation. |
| Persistent service tokens | `PersistentServiceToken` generates 48 random bytes with Java `SecureRandom`, stores them in a deployment-specific file, and compares tokens with `MessageDigest.isEqual`. Explicit operator-provided tokens require separate entropy assessment. |
| Invitation-link encryption | `AESUtil` uses AES-256-GCM, a 12-byte IV from `SecureRandom`, and a 128-bit tag. It accepts a 64-character hexadecimal key. Key ownership and rotation are separate from algorithm/key-length selection. |
| Model credential encryption | `RSAUtil` generates 2048-bit RSA key pairs through Java's cryptography provider. Its existing PKCS#1 v1.5 encryption format is retained for client compatibility; imported key files require operator review. The `ECB` component of Java's RSA transformation name does not describe an AES-ECB operation. |
| Request signing | Python `hmac`/`hashlib` and Java's cryptography APIs implement the standard HMAC-SHA-256 signing paths. |
| Casdoor JWT signing | On fresh initialization, Casdoor v2.67.0 creates `cert-built-in` before importing templates. Without separately supplied token certificate files, its RS256 certificate uses a newly generated 4096-bit RSA key pair. Applications reference that certificate by name. |

The Casdoor templates do not supply a signing private key. Keep
`initDataNewOnly = true` so restarting or upgrading does not overwrite existing
identity data. Removing a sample from a template does **not** rotate a certificate
already stored in a persistent database. Manage existing signing keys through
Casdoor's certificate and application administration, taking active sessions and
JWT verification into account.

## External compatibility paths

The Link plugin's MD5 authorization path is selected by an explicit OpenAPI
header default of `Authorization: MD5`; it is not the general request-signing
default. Other provider-specific request formats retain MD5/HMAC-SHA-1 components
for interoperability. Do not select these formats for new internal authentication
protocols. Use an authenticated HTTPS endpoint, protect the provider credential,
and prefer a stronger protocol when the provider supports one. Compatibility
with a provider does not by itself establish a security-standard exception.

The WeChat adapter retains the platform's SHA-1 signature and AES-CBC message
format. Its active callback path verifies and decrypts inbound messages using
the request's timestamp and nonce. The outbound helper now uses `SecureRandom`
for its 16-character random plaintext prefix; the current callback does not call
that outbound helper. The prefix is distinct from the caller-supplied protocol
nonce. Changing the randomness source preserves the wire format; changing the
signature or cipher requires a compatible platform protocol change.

## Transport and forward secrecy

The Compose service network and example gateway routes use HTTP. Deploy public
endpoints behind a correctly configured TLS terminator, and keep internal service
networks private. Perfect forward secrecy depends on the actual TLS endpoint,
protocol and negotiated cipher suite; it is not enforced by the supplied HTTP
Compose configuration. HTTPS used to download project releases does not establish
forward secrecy for application traffic.

## References and tests

- [Casdoor v2.67.0 initialization](https://github.com/casdoor/casdoor/blob/v2.67.0/main.go)
- [Casdoor built-in certificate](https://github.com/casdoor/casdoor/blob/v2.67.0/object/init.go#L230)
- [Casdoor certificate generation](https://github.com/casdoor/casdoor/blob/v2.67.0/object/cert.go)
- `python docker/astronAgent/casdoor/test_certificate_config.py` checks that sample
  configuration contains no static private key and that application certificate
  references resolve to the upstream-generated certificate.
- `WXBizMsgCryptTest` checks reply framing, encryption/decryption compatibility,
  and rejection of a mismatched nonce signature. The standard backend test suite
  runs it. From `console/backend`, a focused run is:

  ```bash
  mvn -pl hub -am -Dtest=WXBizMsgCryptTest -Dsurefire.failIfNoSpecifiedTests=false test
  ```
