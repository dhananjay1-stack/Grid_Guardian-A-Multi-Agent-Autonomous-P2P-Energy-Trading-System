# Grid Guardian — Dataset Fix Report

**Run UTC:** 2026-02-27T20:52:54.265455+00:00  
**Seed:** 42  
**Mode:** APPLY  
**Target acceptance:** 0.05  

## Fixes Applied

### A. Mass-balance (net_kw)
- Rows corrected: **14081**

### B. Synthetic Voltage / Current
- `voltage_v` synthetic fills: **77760**
- `current_a` synthetic fills: **77597**

### C. Archetype Autotune
- `small_apartment_01`: was **0** offers → params relaxed: {'min_offer_kwh_relaxed': 0.020000000000000004, 'soc_margin_for_offer': 0.05, 'offer_price_markup': 0.88}

### D. Offer Re-pricing & Matching
- Acceptance before: **0.0009**
- Acceptance after:  **0.0501**
- Offers re-priced: **549**
- Synthetic trades created: **549**

### E. Missing Salts
- Salts generated/patched: **7858**

## Sanity Check Results

| Check | Result |
|-------|--------|
| Header match (23 cols) | ✅ PASS |
| Mass balance max error | 4.44e-16 (✅) |
| SoC safety violations | 0 (✅) |
| Offer commit_hash coverage | 11168/11168 (✅) |
| Trade→offer integrity | orphaned=0 (✅) |
| Acceptance rate | 0.0501 |
| PV missing % | 0.113% |
| **Overall** | ✅ ALL PASS |

## Reverting to Originals
Originals are stored in `--backup-dir`. Copy them back to restore.