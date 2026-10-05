# Secret management security tests — #56

Future implementation must include deterministic tests using synthetic secrets only.

## Storage

- [ ] recoverable provider secret is never persisted as plaintext;
- [ ] DB envelope does not contain plaintext DEK/KEK;
- [ ] non-recoverable client credential stores verifier only;
- [ ] wrong AAD/credential metadata causes authenticated decryption failure;
- [ ] modified ciphertext/tag causes failure;
- [ ] unknown/revoked key version fails closed.

## Backoffice and authorization

- [ ] Backoffice create/import returns metadata, not stored plaintext;
- [ ] ordinary Backoffice read cannot reveal an existing provider secret;
- [ ] client/service principal cannot resolve provider SecretRef;
- [ ] finance/reporting role cannot resolve provider SecretRef;
- [ ] provider worker can resolve only an authorized active credential;
- [ ] cross-provider/cross-credential reference substitution is denied.

## Rotation

- [ ] new provider credential version can become ACTIVE without mutating historical attempt references;
- [ ] revoked version cannot be selected for new execution;
- [ ] KEK re-wrap changes wrapped DEK/key version but preserves decrypted synthetic secret;
- [ ] interrupted re-wrap can resume idempotently;
- [ ] algorithm/version migration preserves active synthetic secret and metadata integrity.

## Redaction

For a canary secret value injected only in test memory:
- [ ] structured logs contain no canary;
- [ ] exception text contains no canary;
- [ ] trace attributes contain no canary;
- [ ] metrics labels contain no canary;
- [ ] queue/outbox/event payload contains no canary;
- [ ] API response contains no canary after persistence;
- [ ] secret wrapper repr/str is redacted.

## Outbound HTTP

- [ ] provider secret appears only in the adapter's intended auth field;
- [ ] redirects cannot leak Authorization/provider secret to unapproved host;
- [ ] TLS verification cannot be disabled by a client request;
- [ ] arbitrary client-supplied provider base URL is rejected;
- [ ] provider error body cannot cause secret/log injection.

## Backup/recovery

- [ ] restored synthetic envelope decrypts with restored authorized key material;
- [ ] DB-only restore without KEK cannot recover plaintext;
- [ ] revoked/nonexistent credential fails according to lifecycle policy.

## Repository/CI

- [ ] no real credential fixture;
- [ ] security-baseline secret scan passes;
- [ ] real provider credential not required for test suite.
