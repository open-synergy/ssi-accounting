# Post Journal Entry

## Pre-Condition

- Record is in **Draft** status.
- The entry has at least one journal item.
- The **Journal**, the **Currency**, and every **Account** used on the journal items are
  active (not archived).
- Every **Account** used on the journal items belongs to the entry's company.
- User has _Can Post_ access right (belongs to the Accounting User group or higher).

## Flow

1. Open the **Accounting > Journal Entries** menu.
2. Open the Journal Entry record to post.
3. Click the **Post** button.

## Post-Condition

- Status changes to **Posted**.
- **Name** is filled with the statutory number generated from the journal's sequence —
  gapless per journal, resetting yearly.
- The header fields and the **Journal Items** tab become read-only.
- The **Reset to Draft** button becomes available.

## Note

- If the entry's **Date** falls within a locked accounting period, the system does not
  reject the posting: it moves the entry to the next open date and posts a message in
  the chatter recording the original date, the lock that was violated, and the date
  actually used.
- If any check fails, the system shows an error listing every problem found (an entry
  without lines, an archived journal, currency or account, or an account belonging to a
  different company). The entry stays in **Draft** status. Fix the listed issues and
  post again.
