# Post Journal Entry

## Pre-Condition

- Record is in **Draft** status.
- The entry has at least one journal item.
- The **Journal**, the **Currency**, and every **Account** used on the journal items are
  active (not archived).
- Every **Account** used on the journal items belongs to the entry's company.
- User has _Can Post_ access right (belongs to the Accounting User group or higher).

## Flow

### Single Record

1. Open the **Accounting > Journal Entries** menu.
2. Open the Journal Entry record to post.
3. Click the **Post** button.

### Bulk (Multiple Records)

1. Open the **Accounting > Journal Entries** menu.
2. In the list view, open the **Filters** panel and select **Draft** so only Draft
   records are shown.
3. Select the checkbox of each record to post (or use the header checkbox to select all
   filtered records).
4. Click the **Post** button that appears above the list.

## Post-Condition

- Status changes to **Posted**.
- **Name** is filled with the statutory number generated from the journal's sequence —
  gapless per journal, resetting yearly.
- The header fields and the **Journal Items** tab become read-only.
- The **Reset to Draft** button becomes available.
- When posted in bulk, every selected record moves to **Posted** in the same action. If
  any selected record fails a check, the whole action is cancelled and **none** of the
  selected records are posted — always filter by **Draft** first as described above.

## Note

- If the entry's **Date** falls within a locked accounting period, the system does not
  reject the posting: it moves the entry to the next open date and posts a message in
  the chatter recording the original date, the lock that was violated, and the date
  actually used.
- If any check fails, the system shows an error listing every problem found (a record
  not in Draft, an entry without lines, an archived journal, currency or account, or an
  account belonging to a different company). Fix the listed issues and post again.
