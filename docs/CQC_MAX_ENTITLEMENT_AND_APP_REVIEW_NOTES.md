# CQC Max entitlement boundary and App Review Notes

Status: foundation added September 13, 2026. Purchases remain unavailable until
StoreKit purchase verification and App Store Server Notifications are implemented
and verified in a production-like environment.

## Product boundary

`CQC Max` is a single software-access bundle intended to include Oryntra Pro
and Rule Mirror Pro. It does not include a brokerage connection, trade
execution, asset management, personalized investment advice, CQC equity,
profits, or participation in a managed strategy.

The products keep independent accounts and private data stores. Oryntra owns a
small `cqc_entitlements` ledger containing only an opaque entitlement subject,
product code, status, provider reference, and expiry. It does not contain Rule
Mirror trades, portfolios, journals, passwords, or sessions.

## Current Oryntra implementation

- `cqc_entitlements` is created alongside the existing SQLite schema.
- `cqc_max` gives Oryntra unlimited scanner quota when the existing quota
  policy is enabled.
- `GET /api/cqc/entitlement` is a server-only endpoint. It is disabled by
  default and requires a shared secret, a short-lived request timestamp, and a
  valid HMAC signature. Invalid, expired, or missing credentials fail closed.
- The endpoint returns only effective product codes, an active flag, and expiry.
- The iOS membership sheet now calls the bundle `CQC Max`.

## What must happen before Rule Mirror unlocks CQC Max

1. Configure a vetted private service connection between deployed Oryntra and
   Rule Mirror.
2. Have Rule Mirror request only the signed-in member's opaque entitlement
   subject—not their email, user data, or token—and deny access on any failure.
3. Add a Rule Mirror Pro gate around a real, identified Pro feature; do not
   present access as active until that gate and its tests exist.
4. Make verified StoreKit/JWS handling the only writer of production CQC Max
   records. Manual/debug controls must never write this ledger.
5. Test active, expired, revoked, malformed-signature, unavailable-service,
   and account-deletion cases before enabling purchases.

## Deployment configuration

Keep these out of version control and use a unique random value of at least 32
characters:

```text
CQC_ENTITLEMENT_SHARED_SECRET=<private-random-secret>
CQC_ENTITLEMENT_MAX_CLOCK_SKEW_SECONDS=300
```

Leave the secret blank to keep the endpoint disabled. No existing purchase,
subscription, or app feature is activated by this configuration alone.

## App Review Notes — CQC Max

Use this after the product is created and the entitlement workflow above is
implemented end-to-end:

> CQC Max unlocks Oryntra Pro and Rule Mirror Pro under one active
> subscription. In Oryntra, sign in and open Account > Membership and plans.
> The membership comparison identifies CQC Max and its included software
> access. CQC Max includes unlimited Oryntra scanner reviews, unlimited
> Oryntra workspace capacity, saved research presets and exports, and Rule
> Mirror Pro. The products share only a server-verified subscription
> entitlement; private user records remain separate. Both products are
> educational research and analytics software. Neither connects to a
> brokerage, places trades, manages assets, or provides personalized investment
> advice.

Replace the Rule Mirror navigation sentence with the exact verified screen path
once its membership screen and gate are shipped. Do not submit these notes
while the purchase button still says subscriptions are under maintenance.
