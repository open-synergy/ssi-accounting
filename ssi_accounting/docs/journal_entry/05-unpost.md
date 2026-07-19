# Unpost Journal Entry

## Pre-Condition

- Record is in **Posted** or **Cancelled** status.
- The entry has no reconciled journal items.
- User has _Can Reset to Draft_ access right (belongs to the Accounting User group or
  higher).

## Flow

1. Open the **Accounting > Journal Entries** menu.
2. Open the Journal Entry record to unpost.
3. Click the **Reset to Draft** button.

## Post-Condition

- Status returns to **Draft**.
- The header fields and the **Journal Items** tab become editable again.
- **Name** is deliberately kept — the entry retains the statutory number it was given,
  and reuses that same number when it is posted again.
- The **Post** button becomes available again.

## Note

- The **Reset to Draft** button is only shown on entries in **Posted** or **Cancelled**
  status, so a Draft entry offers no way to trigger this action.
- If the entry has reconciled journal items, the system shows an error and the status is
  left unchanged. Remove the reconciliation first.
