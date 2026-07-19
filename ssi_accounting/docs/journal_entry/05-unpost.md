# Unpost Journal Entry

## Pre-Condition

- Record is in **Posted** or **Cancelled** status.
- The entry has no reconciled journal items.
- User has _Can Reset to Draft_ access right (belongs to the Accounting User group or
  higher).

## Flow

### Single Record

1. Open the **Accounting > Journal Entries** menu.
2. Open the Journal Entry record to unpost.
3. Click the **Reset to Draft** button.

### Bulk (Multiple Records)

1. Open the **Accounting > Journal Entries** menu.
2. In the list view, open the **Filters** panel and select **Posted** and **Cancelled**
   together (multiple filters combine as OR) so only records that can be reset are
   shown.
3. Select the checkbox of each record to unpost (or use the header checkbox to select
   all filtered records).
4. Click the **Reset to Draft** button that appears above the list.

## Post-Condition

- Status returns to **Draft**.
- The header fields and the **Journal Items** tab become editable again.
- **Name** is deliberately kept — the entry retains the statutory number it was given,
  and reuses that same number when it is posted again.
- The **Post** button becomes available again.
- When unposted in bulk, every selected record returns to **Draft** in the same action.

## Note

- If any selected record is not in **Posted** or **Cancelled** status, the system shows
  an error and none of the selected records are reset.
- If any selected record has reconciled journal items, the system shows an error and
  none of the selected records are reset. Remove the reconciliation first.
