# Create Journal Entry

## Pre-Condition

- At least one Journal is configured.
- At least two Accounts are configured, so the entry can be balanced.
- User has _Can Create Journal Entry_ access right (belongs to the Accounting User group
  or higher).

## Flow

1. Open the **Accounting > Journal Entries** menu.
2. Click the **New** button.
3. Fill in the required fields:
   - **Journal**: Select the journal this entry is posted to. Only journals belonging to
     the current company are selectable.
   - **Date**: Defaults to today. Change if the entry is accounted on a different date.
4. Optionally fill in the other fields available in Draft status:
   - **Name**: Leave empty. The statutory number is assigned automatically on posting;
     the greyed-out placeholder previews the number this entry will receive. Fill it in
     only when a specific number must be forced.
   - **Reference**: Enter a free-form external reference for this entry.
   - **Partner**: Select the partner this entry relates to, if any.
   - **Currency**: Automatically filled from **Journal**. Change if the amounts are
     encoded in a different currency. Only visible when multi-currency is enabled.
   - **Company**: Automatically filled from **Journal**. Only visible when multi-company
     is enabled.
   - **Terms and Conditions**: In the **Other Info** tab, enter a free-form note for
     this entry.
5. Add lines in the **Journal Items** tab. Repeat the following steps as many times as
   needed:
   - Click **Add a line**.
   - Fill in each line with:
     - **Account**: Select the account this line posts to.
     - **Label**: Enter a description for this line.
     - **Partner**: Select the partner this line relates to, if it differs from the
       partner on the entry.
     - **Debit**: Enter the debit amount, or leave it at zero for a credit line.
     - **Credit**: Enter the credit amount, or leave it at zero for a debit line.
6. Click **Save**.

## Post-Condition

- A new Journal Entry record is created in **Draft** status.
- **Name** remains empty until the entry reaches **Posted** status.
- The **Total Debit** and **Total Credit** shown below the **Journal Items** tab are
  equal.

## Note

- A journal entry must always be balanced — total Debit must equal total Credit. If the
  lines do not balance, saving is rejected with an error listing the entry and the
  amount it is out by.
- The entry is balanced automatically **only** when the selected **Journal** has a
  Suspense Account configured; the difference is then posted to that account instead of
  the save being rejected. Without a Suspense Account on the journal, the lines must be
  balanced by hand before saving.
