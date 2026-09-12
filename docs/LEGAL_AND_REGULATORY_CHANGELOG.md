# Legal and Regulatory Changelog

## Scope and status

**Last reviewed:** September 8, 2026. This is a U.S.-focused product-control
record for Oryntra’s current described model: self-directed market-research
software, no custody, no brokerage connection, no order routing, no customer
capital, no pooled vehicle, and no personalized investment advice. It is not a
legal opinion, registration determination, or release approval. Laws, state
thresholds, contracts, and app-store rules can change; qualified Florida
corporate, securities, privacy, tax, and payments counsel must approve the
actual operating model before launch.

Oryntra remains in the pre-formation legal configuration until CQC is formed
and a written Oryntra IP assignment is complete. The deployment flag must stay
false until both facts are confirmed; no source-code change establishes either
fact.

## 2026-09-08 — ownership and product-boundary controls

### Shipped controls

- Added one server-side legal-operator configuration for the Terms, Privacy,
  Risk, Methodology, Refund, and Contact routes. The default public statement
  is: “Oryntra is in development and is not yet operated by Cowles Quantitative
  Corporation.”
- Removed personal/minor-founder contact details from canonical legal pages;
  legal contact is deployment-configured.
- Recorded that public subscriptions, if launched, are software access only:
  they grant no CQC equity, profit participation, managed-account access,
  customer-fund relationship, or access to proprietary trading results.
- Disabled the configurable Portfolio Lab directive ledger by default and made
  it available only to a server-provisioned internal-operator role. A plan,
  email address, or client-side UI cannot unlock it.
- Limited public Quant Lab on web and iOS to a frozen historical profile;
  internal model, allocation, risk, and execution controls are not a public
  subscription feature.
- Added a private, Git-ignored family/counsel formation record at
  `docs/private/CQC_FORMATION_READINESS.md`.

### Required before claiming CQC ownership or operation

1. Form the entity and confirm its actual legal name, registered agent,
   officers, authority, tax setup, bank authority, and Apple/payment/data-vendor
   contracting party.
2. Execute and retain written assignments covering existing code, domains,
   brand assets, models, and contributor work.
3. Have the adult legal owner and counsel confirm the facts; only then set
   `ORYNTRA_CQC_FORMED_AND_IP_ASSIGNED=true` and re-review public pages and
   platform metadata.

## Regulatory applicability register

| Area | Current assessment | Product rule / launch gate |
| --- | --- | --- |
| **Investment Advisers Act of 1940 and state adviser law** | Conditional, high importance. Adviser analysis turns on the facts, including advice about securities, being in the business, and compensation. | Keep outputs generalized and research-oriented; no individualized recommendations, discretionary management, or compensation tied to securities advice. Counsel must analyze federal and state registration before any personalized, paid advisory, model-portfolio, or managed-account feature. |
| **SEC adviser marketing rule** | Conditional. Rule 206(4)-1 applies to SEC-registered or required-to-register advisers, not merely to a research app. | Never market backtests, alpha, testimonials, or proprietary results as subscriber outcomes. If CQC/Oryntra becomes an adviser, require written marketing policies, substantiation, fair-and-balanced risk/limitation treatment, records, and adviser-specific performance review before any public performance claim. |
| **Broker-dealer / FINRA / Exchange Act** | Not currently triggered by the described product, because Oryntra does not effect transactions, hold itself out as a broker, or receive transaction compensation. FINRA rules govern member firms. | Do not add brokerage account linking, order routing, transaction-based compensation, trade execution, account custody, or language telling a particular person to buy/sell without a broker-dealer and securities-law review. A disclaimer does not cure regulated activity. |
| **Investment Company Act / pooled capital** | Not currently triggered by the described product because users do not contribute capital or receive an interest in a pooled vehicle. | Keep CQC proprietary capital operationally, financially, and contractually separate. Do not sell interests, pool user funds, share trading profits, or create copy-trading/investment participation without specialist counsel. |
| **FinCEN, Bank Secrecy Act, and state money-transmitter law** | Not currently triggered by research software that neither accepts nor transmits customer funds or value. The analysis is fact-specific. | Do not receive, hold, transfer, convert, settle, or direct customer funds/crypto. Before any wallet, cash balance, payment facilitation beyond a normal merchant checkout, crypto, or remittance feature, obtain FinCEN and state money-transmitter analysis plus a BSA/AML program decision. |
| **OFAC sanctions and export controls** | Conditional. U.S. software/service providers need sanctions-aware controls as their service, geography, customers, and hosting change. | Before international availability, payments, or enterprise access, counsel must determine screening, geoblocking, export classification, and restricted-jurisdiction controls. Preserve a documented sanctions-risk assessment. |
| **FTC Act, state UDAP laws, and reviews/endorsements** | Applicable to public marketing now. Claims must be truthful, non-misleading, and substantiated. | No “guaranteed,” “winning,” or implied typical-return claims. Disclose material connections with endorsers/influencers; do not buy, suppress, or fabricate reviews. Keep dated evidence for every factual performance or product claim. |
| **Online subscriptions, auto-renewal, refunds, and payments** | Conditional until paid subscriptions/checkouts are enabled; then high importance. | Before charging: clearly show price, cadence, renewal, cancellation, trial conversion, and material limits at consent; retain consent records; offer simple cancellation; assess federal negative-option rules, card-network terms, Apple rules, and each targeted state’s auto-renewal law. Do not enable payment merely because a pricing page exists. |
| **Privacy, data security, and breach response** | Applicable now for collected account, device, usage, support, and provider-connection data. State privacy statutes depend on thresholds and data practices; breach laws are state-specific. | Maintain a data inventory, vendor list, retention/deletion schedule, access controls, incident response process, and truthful privacy notice. Reassess CCPA/CPRA and other state-law thresholds before targeted advertising, analytics expansion, or scale. Do not sell/share personal information for cross-context behavioral advertising without the required notice and choice where applicable. |
| **GLBA / FTC Safeguards Rule** | Conditional. The FTC describes GLBA coverage around financial products/services and notes that coverage depends on the activities, not branding. | Current research-only positioning lowers the risk but does not decide it. If Oryntra/CQC gives financial advice, handles covered customer information, or enters another financial-institution activity, obtain a formal GLBA/Safeguards review and implement the required written information-security program. |
| **COPPA / youth privacy** | Conditional. Oryntra is not child-directed, but COPPA applies if a service is directed to children under 13 or has actual knowledge it collects their personal information. | Keep the service for general/older audiences; do not knowingly collect under-13 data without the required notice and verifiable parental consent. Reassess if youth marketing, school distribution, or age collection is introduced. |
| **ADA / accessibility** | Accessibility is an ongoing legal and product risk for a public digital service. | Maintain keyboard use, focus states, readable contrast, labels, error feedback, and mobile accessibility; test material releases against WCAG-informed criteria. Legal counsel should assess jurisdiction-specific ADA exposure. |
| **Apple App Store rules and platform privacy** | Applicable to the iOS release. Financial-services apps should be submitted by the legal entity providing the service; apps with accounts must offer in-app deletion; subscriptions and privacy disclosures have additional requirements. | Before release: ensure the adult/entity account holder and App Store Connect seller are correct, App Privacy answers match actual SDK/data flows, privacy policy is linked in-app and in metadata, account deletion works, and any subscription provides ongoing value and clear terms. |
| **Corporate, tax, consumer, and contract law** | Applicable when CQC is formed or Oryntra charges users. | Obtain Florida entity/tax advice; use executed IP and contractor agreements; verify sales-tax treatment, payment-processor terms, record retention, refund handling, and insurance. Do not call CQC formed, registered, insured, licensed, or trademarked until independently verified. |
| **Market-data licensing and intellectual property** | Contractual and potentially high risk, not solved by a securities disclaimer. | Use provider data only within the relevant plan/license; do not redistribute raw data or claim real-time coverage without written rights. Retain provider agreements and recheck them before commercial/public display. |
| **Email, SMS, and affiliate marketing** | Conditional. No current authorization to send promotional messages is established by this codebase. | Before campaigns, implement consent, unsubscribe/suppression, sender identity, recordkeeping, and a CAN-SPAM/TCPA/state-law review. Never add SMS marketing or referral compensation by default. |

## Operating prohibitions preserved by the current design

The following are not release features and require a new legal review before
they are proposed or built: brokerage integrations; order placement or routing;
customer-fund custody; pooled capital; copy trading; revenue sharing; payment
for transaction referrals; individualized security recommendations; adviser
representations; public claims of verified alpha or strategy returns; and
subscription access to CQC’s proprietary trading activity.

## Release-gate checklist

- [ ] Adult legal owner/entity and contracts confirmed; CQC ownership flag stays
      false until formation **and** written IP assignment are confirmed.
- [ ] Securities counsel gives a written product-boundary and state-law review.
- [ ] Payments/subscription counsel or qualified reviewer approves the actual
      checkout, cancellation, refund, tax, and record-retention flows.
- [ ] Privacy/data map, vendor/SDK inventory, retention schedule, deletion
      process, incident plan, and state-threshold review are completed.
- [ ] Marketing inventory is reviewed for substantiation, risk disclosure,
      testimonials, affiliate relationships, and absence of performance promises.
- [ ] Apple metadata, privacy answers, account deletion, sign-in, subscription,
      and financial-services entity requirements are checked against the shipped
      binary—not only source code.
- [ ] Market-data vendor rights and all production environment variables are
      reviewed; no secrets, database, cache, or customer records are deployed
      from a source archive.

## Primary sources

1. U.S. Securities and Exchange Commission, [Regulation of Investment
   Advisers](https://www.sec.gov/about/offices/oia/oia_investman/rplaze-042012.pdf).
2. U.S. Securities and Exchange Commission, [Investment Adviser
   Marketing](https://www.sec.gov/resources-small-businesses/small-business-compliance-guides/investment-adviser-marketing).
3. FINRA, [Regulation Best
   Interest](https://www.finra.org/rules-guidance/key-topics/regulation-best-interest).
4. FinCEN, [Money Services Business: Am I an
   MSB?](https://www.fincen.gov/am-i-msb) and [payment-processor administrative
   ruling](https://www.fincen.gov/resources/statutes-regulations/administrative-rulings/application-money-services-business).
5. U.S. Treasury OFAC, [Framework for OFAC Compliance
   Commitments](https://ofac.treasury.gov/recent-actions/20190502_33).
6. Federal Trade Commission, [Advertisement
   Endorsements](https://www.ftc.gov/news-events/topics/truth-advertising/advertisement-endorsements),
   [Consumer Reviews and Testimonials Rule Q&A](https://www.ftc.gov/business-guidance/resources/consumer-reviews-testimonials-rule-questions-answers),
   and [Negative Option Rule guidance](https://www.ftc.gov/business-guidance/blog/2024/10/click-cancel-ftcs-amended-negative-option-rule-what-it-means-your-business).
7. California Privacy Protection Agency, [CCPA consumer
   rights](https://privacy.ca.gov/california-privacy-rights/rights-under-the-california-consumer-privacy-protection-act/).
8. Federal Trade Commission, [COPPA Rule](https://www.ftc.gov/legal-library/browse/rules/childrens-online-privacy-protection-rule-coppa) and [GLBA / Safeguards
   Rule](https://www.ftc.gov/legal-library/browse/rules/safeguards-rule).
9. U.S. Department of Justice, [web accessibility guidance under the
   ADA](https://www.justice.gov/archives/opa/pr/justice-department-issues-web-accessibility-guidance-under-americans-disabilities-act).
10. Apple, [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/),
    [account deletion guidance](https://developer.apple.com/support/offering-account-deletion-in-your-app/),
    and [App Privacy Details](https://developer.apple.com/app-store/app-privacy-details/).
