# Bootstrap and break-glass security tests — #58

## First-admin bootstrap

- [ ] no active bootstrap grant => redemption denied;
- [ ] expired/revoked/consumed grant => denied;
- [ ] wrong issuer/subject binding => denied;
- [ ] insufficient MFA strength => denied;
- [ ] correct bound subject + MFA + token creates first admin;
- [ ] token is consumed atomically;
- [ ] second concurrent redemption cannot create another first admin;
- [ ] crash/retry after successful commit cannot reuse token;
- [ ] first-admin creation rotates the browser session/security state.

## Token handling

- [ ] DB stores verifier only;
- [ ] clear bootstrap/recovery token shown once;
- [ ] clear token not in URL/log/trace/audit/error;
- [ ] comparison is constant-time;
- [ ] suspicious repeated failures are rate-limited/auditable.

## Post-bootstrap

- [ ] ordinary bootstrap route is unavailable after an active Backoffice admin exists;
- [ ] knowing an old bootstrap token does not grant privilege;
- [ ] new Backoffice admins are created only through normal authorized administration.

## Break-glass

- [ ] recovery grant is bound to intended OIDC subject;
- [ ] recovery elevation requires strong MFA;
- [ ] grant expires automatically;
- [ ] elevation is narrow/purpose-bound;
- [ ] provider secret/finance/client access is not inherited unless explicitly part of the recovery permission;
- [ ] recovery grant cannot be converted into permanent credential;
- [ ] use creates security audit/alert event;
- [ ] post-recovery revocation returns principal to normal authorization.

## IdP failure

- [ ] IdP outage does not enable local password/master-key web login;
- [ ] infrastructure recovery tooling cannot be invoked through normal client API;
- [ ] configuration repair path is limited/audited.

## Negative privilege tests

- [ ] CUSTOMER_HUMAN cannot redeem Backoffice recovery grant;
- [ ] SERVICE_CLIENT cannot redeem human bootstrap/recovery;
- [ ] request-supplied role/admin flag cannot affect grant authorization;
- [ ] first unauthenticated public requester cannot claim bootstrap by racing.
