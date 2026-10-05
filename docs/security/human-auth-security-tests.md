# Human authentication security contract tests — #54

These are mandatory contract/security cases for the future implementation.

## OIDC flow

- [ ] login uses Authorization Code + PKCE S256;
- [ ] missing/wrong state fails closed;
- [ ] missing/wrong nonce fails closed where required;
- [ ] wrong issuer/audience/signature/algorithm/expiry fails closed;
- [ ] redirect URI is exact/registered;
- [ ] attacker-controlled return_url cannot create open redirect;
- [ ] authorization code/PKCE verifier/token never enters logs.

## Session fixation and rotation

- [ ] pre-auth session identifier is not retained after login;
- [ ] session ID rotates after successful login;
- [ ] session ID rotates after privilege elevation/recovery where required;
- [ ] stolen expired/revoked session ID cannot be reused;
- [ ] cookie is Secure + HttpOnly and expected SameSite/host/path policy;
- [ ] server-side expiry is enforced even if browser keeps a cookie.

## Logout and revocation

- [ ] logout invalidates server-side session;
- [ ] privilege removal/suspension invalidates or blocks high-value actions;
- [ ] subject-wide administrative revocation works;
- [ ] stale session authorization cannot bypass authoritative membership status.

## CSRF

- [ ] state-changing request without CSRF token is rejected;
- [ ] wrong-session CSRF token is rejected;
- [ ] cross-site Origin on state-changing request is rejected according to policy;
- [ ] CORS setting alone does not make a CSRF test pass;
- [ ] safe GET does not perform state mutation.

## MFA / step-up

- [ ] Backoffice session without required MFA strength cannot perform privileged action;
- [ ] credential create/rotate/revoke requires recent step-up;
- [ ] request header/body cannot forge authentication strength;
- [ ] expired authentication freshness triggers step-up;
- [ ] successful step-up resumes only the bound authorized action/context.

## Recovery

- [ ] recovery token/transaction is short-lived and one-time;
- [ ] reused recovery token fails;
- [ ] recovery codes are one-time and previous set invalidates on regeneration;
- [ ] recovery completion creates audit evidence and revokes sessions according to policy;
- [ ] recovery does not reveal whether an unrelated account exists;
- [ ] support/admin cannot bypass recovery through an ordinary endpoint.

## Tenant/authorization

- [ ] customer session cannot become Backoffice by changing route/body/tenant ID;
- [ ] customer A cannot use a valid session to access tenant B;
- [ ] step-up does not bypass object/property authorization;
- [ ] Backoffice privilege is server-side and not inferred from IdP login alone.

## Sensitive-data handling

- [ ] no OAuth token/session cookie/TOTP secret/recovery code in logs/traces;
- [ ] error responses do not include raw IdP payload/token;
- [ ] client-side bundle/storage contains no bearer refresh/access token.
