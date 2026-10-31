# Stripe integration TODO

The server now exposes `POST /api/auth/checkout-session` and creates a hosted
recurring Checkout Session. It does not activate subscriptions locally until a
verified webhook/entitlement flow is added.

## Values to Replace

The following values are placeholders and must be updated before going live.

**Files containing placeholders:**

- [server/.env.example](server/.env.example)

| Field | Current Value | What to Set |
|---|---|---|
| `STRIPE_SECRET_KEY` | empty | A server-side Stripe restricted key (`rk_...`) with only the permissions needed for Checkout/Billing. |
| `STRIPE_PRICE_PRO` | empty | The recurring Stripe Price ID for the Pro plan (`price_...`). |
| `STRIPE_PRICE_MAX` | empty | The recurring Stripe Price ID for the Max plan (`price_...`). |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8001` | The HTTPS public Oryntra origin before accepting real payments. |

## Remaining setup

1. Install the pinned `stripe` dependency from `server/requirements.txt`.
2. Add the variables above only to the server's private environment; never put
   `STRIPE_SECRET_KEY` in the browser or mobile bundle.
3. Confirm Stripe Tax registrations and tax settings before relying on
   `automatic_tax.enabled=true`.
4. Add and verify a signed Stripe webhook that updates the existing
   `subscriptions` table from Checkout/Billing events. Until that exists,
   Checkout can collect a payment but Oryntra will not automatically grant or
   revoke plan access.
5. Replace the placeholder success/cancel routes with the deployed billing
   pages if those paths differ.

The current endpoint intentionally does not use the private manual subscription
activator and does not transmit orders or broker credentials.
