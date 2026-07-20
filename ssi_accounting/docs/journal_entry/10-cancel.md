# Cancel Journal Entry

## Pre-Condition

- Record is in **Draft** or **Posted** status.
- User has _Can Cancel_ access right (belongs to the Accounting User group or higher).

## Flow

1. Open the **Accounting > Journal Entries** menu.
2. Open the Journal Entry record to cancel.
3. Click the **Cancel** button.
4. Click **OK** on the confirmation dialog.

## Post-Condition

- Status changes to **Cancelled**.
- The header fields and the **Journal Items** tab stay read-only.
- **Name** is kept, so the statutory number of a cancelled entry remains traceable.
- The **Reset to Draft** button becomes available, so a cancelled entry can still be
  returned to Draft — see [Unpost Journal Entry](05-unpost.md).
