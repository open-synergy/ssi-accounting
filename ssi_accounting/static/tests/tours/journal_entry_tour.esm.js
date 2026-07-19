/* Copyright 2026 OpenSynergy Indonesia
 * Copyright 2026 PT. Simetri Sinergi Indonesia
 * License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl). */

import {registry} from "@web/core/registry";

/* Every tour below executes one Instruction Work (IK) file, step for step.
 * The `// -- Flow N` comments name the IK sentence each block comes from, so
 * the tour/IK correspondence stays auditable at a glance. Assertions are
 * limited to what the user can see (view rendered, button available, status
 * bar moved) -- field values, computes and totals belong to the YAML unit
 * tests in `tests/test_data_journal_entry*.yaml`. */

/* Opening the Accounting > Journal Entries menu is Flow step 1 of every IK,
 * so it is the first block of every tour. */
const openJournalEntriesMenu = () => [
    {
        content: "Open the apps menu",
        trigger: ".o_navbar_apps_menu button",
        run: "click",
    },
    {
        content: "Open the Accounting app",
        trigger: '.o_app[data-menu-xmlid="ssi_accounting.menu_accounting_root"]',
        run: "click",
    },
    {
        content: "Open the Journal Entries menu",
        trigger:
            '.o_menu_sections [data-menu-xmlid="ssi_accounting.journal_entry_menu"]',
        run: "click",
    },
    {
        // Wait for the list to render -- assertion step, performs no action.
        trigger: ".o_list_view",
    },
];

// IK: docs/journal_entry/01-create.md
registry.category("web_tour.tours").add("ssi_accounting_journal_entry_create", {
    url: "/odoo",
    steps: () => [
        // -- Flow 1 -- Open the Accounting > Journal Entries menu
        ...openJournalEntriesMenu(),

        // -- Flow 2 -- Click the New button
        {
            content: "Click the New button",
            trigger: ".o_list_button_add",
            run: "click",
        },
        {
            trigger: ".o_form_view.o_form_editable",
        },

        // -- Flow 3 -- Fill in the required fields (Journal, Date)
        {
            content: "Select the Journal",
            trigger: ".o_field_many2one[name='journal_id'] input",
            run: "edit TOUR-JOURNAL",
        },
        {
            content: "Pick the journal from the dropdown",
            trigger: ".o-autocomplete--dropdown-menu li:contains(TOUR-JOURNAL)",
            run: "click",
        },
        {
            content: "Set the Date",
            trigger: ".o_field_widget[name='date'] input",
            run: "edit 03/10/2026 && press Escape",
        },

        // -- Flow 4 -- Optionally fill in the other fields available in Draft
        {
            content: "Fill in the Reference",
            trigger: ".o_field_widget[name='ref'] input",
            run: "edit TOUR-CREATE-001",
        },

        // -- Flow 5 -- Add lines in the Journal Items tab
        {
            content: "Open the Journal Items tab",
            trigger: ".o_notebook .nav-link:contains(Journal Items)",
            run: "click",
        },
        {
            content: "Add the debit line",
            trigger: ".o_field_x2many .o_field_x2many_list_row_add a",
            run: "click",
        },
        {
            content: "Select the debit Account",
            trigger: ".o_selected_row .o_field_many2one[name='account_id'] input",
            run: "edit TOURDR",
        },
        {
            content: "Pick the debit account from the dropdown",
            trigger: ".o-autocomplete--dropdown-menu li:contains(TOURDR)",
            run: "click",
        },
        {
            content: "Fill in the debit Label",
            trigger: ".o_selected_row .o_field_widget[name='name'] input",
            run: "edit Tour debit line",
        },
        {
            content: "Fill in the Debit amount",
            trigger: ".o_selected_row .o_field_widget[name='debit'] input",
            run: "edit 100.00",
        },
        {
            content: "Add the credit line",
            trigger: ".o_field_x2many .o_field_x2many_list_row_add a",
            run: "click",
        },
        {
            content: "Select the credit Account",
            trigger: ".o_selected_row .o_field_many2one[name='account_id'] input",
            run: "edit TOURCR",
        },
        {
            content: "Pick the credit account from the dropdown",
            trigger: ".o-autocomplete--dropdown-menu li:contains(TOURCR)",
            run: "click",
        },
        {
            content: "Fill in the credit Label",
            trigger: ".o_selected_row .o_field_widget[name='name'] input",
            run: "edit Tour credit line",
        },
        {
            content: "Fill in the Credit amount",
            trigger: ".o_selected_row .o_field_widget[name='credit'] input",
            run: "edit 100.00",
        },

        // -- Flow 6 -- Click Save
        {
            content: "Save the record",
            trigger: ".o_form_button_save",
            run: "click",
        },
        {
            trigger: ".o_form_view:not(.o_form_editable)",
        },

        // -- Post-Condition -- A new record is created in Draft status
        {
            content: "Status is Draft",
            trigger:
                ".o_statusbar_status button[data-value='draft'].o_arrow_button_current",
        },
        // -- Post-Condition -- The Post button is available on a Draft entry
        {
            content: "The Post button is available",
            trigger: ".o_statusbar_buttons button[name='action_post']",
        },
    ],
});

// IK: docs/journal_entry/04-post.md
registry.category("web_tour.tours").add("ssi_accounting_journal_entry_post", {
    url: "/odoo",
    steps: () => [
        // -- Flow 1 -- Open the Accounting > Journal Entries menu
        ...openJournalEntriesMenu(),

        // -- Flow 2 -- Open the Journal Entry record to post
        {
            content: "Open the journal entry to post",
            trigger: ".o_data_row:contains(TOUR-POST-001) .o_data_cell:first",
            run: "click",
        },
        {
            trigger: ".o_form_view",
        },

        // -- Flow 3 -- Click the Post button
        {
            content: "Click the Post button",
            trigger: ".o_statusbar_buttons button[name='action_post']",
            run: "click",
        },

        // -- Post-Condition -- Status changes to Posted
        {
            content: "Status is Posted",
            trigger:
                ".o_statusbar_status button[data-value='posted'].o_arrow_button_current",
        },
        // -- Post-Condition -- The Reset to Draft button becomes available
        {
            content: "The Reset to Draft button is available",
            trigger: ".o_statusbar_buttons button[name='button_draft']",
        },
        // -- Post-Condition -- The Journal field becomes read-only
        {
            content: "The Journal field is read-only",
            trigger: ".o_field_widget[name='journal_id'].o_readonly_modifier",
        },
    ],
});

// IK: docs/journal_entry/05-unpost.md
registry.category("web_tour.tours").add("ssi_accounting_journal_entry_unpost", {
    url: "/odoo",
    steps: () => [
        // -- Flow 1 -- Open the Accounting > Journal Entries menu
        ...openJournalEntriesMenu(),

        // -- Flow 2 -- Open the Journal Entry record to unpost
        {
            content: "Open the journal entry to unpost",
            trigger: ".o_data_row:contains(TOUR-UNPOST-001) .o_data_cell:first",
            run: "click",
        },
        {
            trigger: ".o_form_view",
        },

        // -- Flow 3 -- Click the Reset to Draft button
        {
            content: "Click the Reset to Draft button",
            trigger: ".o_statusbar_buttons button[name='button_draft']",
            run: "click",
        },

        // -- Post-Condition -- Status returns to Draft
        {
            content: "Status is Draft",
            trigger:
                ".o_statusbar_status button[data-value='draft'].o_arrow_button_current",
        },
        // -- Post-Condition -- The Post button becomes available again
        {
            content: "The Post button is available again",
            trigger: ".o_statusbar_buttons button[name='action_post']",
        },
    ],
});
