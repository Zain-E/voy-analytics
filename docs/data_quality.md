# Data-quality notes

Findings from profiling the raw `voy` dataset (read-only), and how each is handled
in the models. Snapshot / as-of date: **2024-08-16** (max date in `activity`).

## Handled in-model

| Finding | Count | Handling |
|---|---:|---|
| Registered customers | 532,848 | `dim_customer` (all) |
| Ever-active customers | 512,366 | retention universe (`fct_customer_per_month_snapshot`) |
| Registered but **never active** | 20,482 | excluded from retention; surfaced as **activation rate** (96.2%) |
| Ever-active customers with **no acquisition taxonomy** | 4,896 | bucketed as `'Unknown'` |
| Malformed spells (`to_date < from_date`) | 0 | filtered in `stg_voy__activity` defensively |
| Orphan keys (activity/acq customer missing from customers) | 0 | source `relationships` tests |

## Structural facts the model relies on

- `customers` and `acq_orders` are **one row per customer**; `activity` is
  **many rows per subscription** (up to 39), and a customer holds a **median of 2**
  subscriptions (max 65). This is why activity is merged to the customer via
  gaps-and-islands before any metric is computed.
- Only **two countries** exist (Brazil 296,693; United Kingdom 236,155). Country
  drill is binary today; the model generalises.
- Acquisition mix is highly skewed: Hair Loss 383k, ED 73k, Weight Loss 42k, then
  a long tail (Other, Sleep, TRT, Mental Health).

## Known limitations / caveats

1. **Snapshot tail under-capture.** The extract holds only *closed* spells, so the
   currently-in-flight billing period is not fully materialised: "live on the final
   day" is ~9,637 customers vs ~195,503 on the 32-day window. **Mitigation:** the
   partial final month is excluded from every trend, and the 32-day window is the
   headline active metric. *Recommend confirming with Voy whether open/in-flight
   subscriptions can be exported.*
2. **No revenue / plan / MRR fields.** Therefore **no NRR/GRR, ARPU, or LTV** — all
   retention/churn here is **logo (customer-count)** based. Adding revenue retention
   would require per-subscription price/plan joined to the spells.
3. **Reactivation is material.** Activity-based retention runs well above survival
   (e.g. 2023-01 cohort at 6 months: 51.8% vs 38.4%), so both curves are reported.

## Validation performed (read-only, against raw)

- Island merge reproduces raw MAU **exactly** (Jul-2023 = 120,492; Jul-2024 = 191,784).
- **Zero** overlapping continuous subscription periods per customer.
- 2023-01 cohort survival decays monotonically and activity ≥ survival at every tenure.
