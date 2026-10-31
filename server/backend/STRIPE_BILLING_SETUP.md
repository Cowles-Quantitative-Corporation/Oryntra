# Oryntra Stripe Billing Setup

This backend includes server-created Stripe Checkout Sessions, a 7-day trial,
webhook-verified entitlements, checkout reconciliation, and Stripe Customer
Portal support.

## Live Stripe resources currently used

- Oryntra Pro product: `prod_VItTaLSrOINd1u`
- Oryntra Pro monthly price: `price_1UIHgIAoOGrQDasxaSEIim8K` ($6.99/mo)
- CQC Max product: `prod_VItsDd2XrUVSMG`
- CQC Max monthly price: `price_1UII4RAoOGrQDasxOUPRTPBk` ($10.99/mo)
- Customer Portal configuration: `bpc_1UIxYTAoOGrQDasxjX3GSp03`

## Required server environment variables

Never put these values in frontend JavaScript or commit the live secret values.

```bash
PUBLIC_BASE_URL=https://oryntraai.com
STRIPE_SECRET_KEY=sk_live_...              # from Stripe Developers > API keys
STRIPE_WEBHOOK_SECRET=whsec_...            # from the Oryntra webhook endpoint
STRIPE_PRICE_ORYNTRA_PRO=price_1UIHgIAoOGrQDasxaSEIim8K
STRIPE_PRICE_CQC_MAX=price_1UII4RAoOGrQDasxOUPRTPBk
STRIPE_PORTAL_CONFIGURATION_ID=bpc_1UIxYTAoOGrQDasxjX3GSp03
STRIPE_TRIAL_DAYS=7
STRIPE_MANAGED_PAYMENTS=true
STRIPE_ALLOW_PROMOTION_CODES=false
```

If CQC Max should relay access to RuleMirror, also configure the same
high-entropy `CQC_ENTITLEMENT_SHARED_SECRET` in both products.

## Webhook endpoint

Configure Stripe to send the following live events to:

`https://oryntraai.com/api/billing/webhook`

Events:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `customer.subscription.paused`
- `customer.subscription.resumed`
- `invoice.paid`
- `invoice.payment_failed`

Copy that endpoint's signing secret into `STRIPE_WEBHOOK_SECRET`.

## Frontend contract

Start checkout (signed-in user):

```http
POST /api/billing/checkout
Content-Type: application/json
Authorization: Bearer <oryntra session>

{"plan_code":"pro"}
```

or `{"plan_code":"cqc_max"}`. Redirect the browser to the returned `url`.

On the Stripe success redirect, read `session_id` from the query string and
call:

`GET /api/billing/checkout-status?session_id=cs_...`

This verifies that the Checkout Session belongs to the signed-in user and
immediately synchronizes the Stripe subscription. Then refresh `/api/auth/me`.

For subscription changes, payment methods, invoices, and cancellation:

```http
POST /api/billing/portal
```

Redirect to the returned `url`.

## Security contract

- The success query string does **not** grant access.
- Only a verified Stripe webhook or authenticated server-side Stripe lookup
  updates the local subscription record.
- Webhook signatures are verified against the exact raw request body.
- Webhook event IDs are stored to make processing idempotent.
- Trials and active subscriptions grant access; canceled/unpaid/incomplete
  Stripe states do not.
