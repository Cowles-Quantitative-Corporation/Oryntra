# iOS App Store Review Readiness

Reviewed against Apple's App Review Guidelines on September 18, 2026. This is
an engineering readiness record, not legal advice and not a promise of App
Review approval.

## What the current iOS binary does

- Oryntra is educational market-research software. It does not connect to a
  brokerage, accept customer money, route an order, hold assets, or provide a
  personalized portfolio service.
- Password account creation, account deletion, Privacy Policy, Terms, risk
  disclosure, methodology, and contact links are reachable in the app.
- The mobile Turnstile flow uses the iOS WebKit-based web view and its token is
  required by the server for password sign-up and sign-in.
- No advertising SDK is included in the current iOS dependency list.
- Current membership screens are informational only: checkout is unavailable
  and the app does not direct a user to purchase Oryntra access elsewhere.

## Review-sensitive constraints already enforced

1. **No hidden paid unlock.** Minerva remains unreleased even for an active
   subscriber. An active subscription is recognized as such; the user is not
   shown a false purchase prompt.
2. **Future iOS subscriptions.** If Pro or CQC Max unlocks any digital feature
   in iOS, implement Apple in-app purchase, restore purchases, price/renewal/
   cancellation disclosures, and a reviewer-visible flow before enabling it.
   Do not substitute a web checkout, a code, an owner override, or a QR link.
3. **Financial-services boundary.** Keep the App Store listing and review notes
   precise: generalized educational research only; no order execution, custody,
   managed account, or individualized recommendation. If the product changes
   into trading, investing, or money-management service, obtain required
   permissions and submit it through the responsible legal entity first.
4. **Privacy and third-party data.** The App Privacy answers must match the
   current policy and implementation, including account data, user-created
   journal/research records, session/security metadata, Turnstile, hosting,
   TradingView, and any provider data request. Confirm written rights for every
   third-party chart and market-data use before release.

## Required submission checks

- Test the release archive on a physical iPhone and an IPv6-only network.
- Ensure every production API and legal/support URL is live during review.
- Supply App Review with a working review account or a fully featured review
  mode, plus exact steps for sign-in and the Turnstile check.
- Use real in-app screenshots that show the scanner, evidence cards, watchlist,
  paper journal, and account controls—not a login or splash screen.
- Complete the App Store privacy questionnaire from the deployed build and
  submit accurate support/contact information, age rating, and review notes.
- Remove placeholder language and do not advertise unreleased models or paid
  access as currently purchasable.

## Suggested App Review note

> Oryntra AI is educational market-research software. It does not connect to a
> brokerage, accept funds, transmit orders, execute trades, or provide
> personalized investment advice. Account creation and sign-in use a mobile
> Cloudflare Turnstile check. Please use the supplied review account and open
> Scanner, Watchlist, Paper Journal, and Account. Membership comparison is
> informational in this build; there is no checkout or external purchase path.
