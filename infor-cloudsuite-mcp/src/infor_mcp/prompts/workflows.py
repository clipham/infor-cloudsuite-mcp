"""
MCP Prompts for Common CloudSuite Finance Workflows

Prompts are pre-crafted instruction sets that help the LLM execute
common multi-step workflows accurately. When a user selects a prompt,
the LLM gets detailed instructions for how to use the available tools
to accomplish the task.

These are the precursors to the Tier 2 agentic workflows — right now
they guide the LLM through the steps, later they'll be automated.
"""

import logging

logger = logging.getLogger("infor_mcp.prompts")


def register_prompts(mcp):
    """Register MCP prompts for common CloudSuite workflows."""

    @mcp.prompt()
    async def ap_aging_analysis() -> str:
        """
        Analyze accounts payable aging — find overdue invoices,
        summarize by vendor and aging bucket, and identify payment priorities.
        """
        return (
            "You are analyzing AP aging for the user. Follow these steps:\n\n"
            "1. Query APInvoice for all open/unpaid invoices:\n"
            '   - Use query_business_class with business_class="APInvoice"\n'
            '   - Fields: "InvoiceNumber,Vendor,VendorName,InvoiceDate,DueDate,InvoiceAmount,AmountDue,Status"\n'
            '   - Filter for open invoices: filter_expr="Status::Open" or similar\n'
            "   - Set limit to 100 to get a comprehensive view\n\n"
            "2. Analyze the results:\n"
            "   - Group invoices by aging bucket (Current, 1-30, 31-60, 61-90, 90+)\n"
            "   - Calculate totals by vendor\n"
            "   - Identify the largest overdue amounts\n\n"
            "3. Present findings:\n"
            "   - Summary table of aging buckets with totals\n"
            "   - Top 5 vendors by overdue amount\n"
            "   - Recommendations for payment prioritization\n"
        )

    @mcp.prompt()
    async def vendor_spend_analysis() -> str:
        """
        Analyze vendor spending patterns — total spend by vendor,
        trends over time, and identification of top vendors.
        """
        return (
            "You are analyzing vendor spend for the user. Follow these steps:\n\n"
            "1. Get a list of recent AP invoices with vendor details:\n"
            '   - Use query_business_class with business_class="APInvoice"\n'
            '   - Fields: "Vendor,VendorName,InvoiceNumber,InvoiceDate,InvoiceAmount,Status"\n'
            "   - Set a high limit (100) to capture a good sample\n\n"
            "2. If the user specified a vendor, use find_record to get vendor details:\n"
            '   - business_class="Vendor", key_values="Vendor={vendor_id}"\n\n'
            "3. Analyze the results:\n"
            "   - Total spend by vendor\n"
            "   - Average invoice amount by vendor\n"
            "   - Invoice frequency by vendor\n"
            "   - Month-over-month trends if date range is sufficient\n\n"
            "4. Present findings with actionable insights.\n"
        )

    @mcp.prompt()
    async def gl_account_inquiry() -> str:
        """
        Investigate general ledger account activity — balances, transactions,
        and budget comparisons for a specific account or fund.
        """
        return (
            "You are investigating GL account activity. Follow these steps:\n\n"
            "1. Query GL detail for the specified account/fund:\n"
            '   - Use query_business_class with business_class="GeneralLedgerDetail"\n'
            '   - Fields: "Company,AccountingUnit,Account,PostingDate,Amount,Description,SourceCode"\n'
            "   - Apply filters based on what the user asked for (account, fund, date range)\n\n"
            "2. For budget comparison, also query Budget records:\n"
            '   - business_class="Budget"\n'
            '   - Match the same account and accounting unit\n\n'
            "3. For account master details:\n"
            '   - Use find_record with business_class="ChartOfAccounts"\n\n'
            "4. Summarize:\n"
            "   - YTD actual spend vs budget\n"
            "   - Transaction detail sorted by date\n"
            "   - Variance analysis if budget data is available\n"
            "   - Flag any unusual transactions\n"
        )

    @mcp.prompt()
    async def purchase_order_status() -> str:
        """
        Check purchase order status — open POs, partially received POs,
        and POs pending approval.
        """
        return (
            "You are checking purchase order status. Follow these steps:\n\n"
            "1. Query open purchase orders:\n"
            '   - Use query_business_class with business_class="PurchaseOrder"\n'
            '   - Fields: "Company,PurchaseOrder,Vendor,VendorName,OrderDate,TotalAmount,Status"\n'
            "   - Filter by status if the user specified (Open, Approved, etc.)\n\n"
            "2. For a specific PO, get line detail:\n"
            '   - Use query_business_class with business_class="PurchaseOrderLine"\n'
            '   - Filter by the PO number\n'
            '   - Fields: "PurchaseOrder,Line,Item,Description,QuantityOrdered,QuantityReceived,UnitPrice,LineAmount"\n\n'
            "3. Summarize:\n"
            "   - Total value of open POs\n"
            "   - POs with partial receipts\n"
            "   - POs awaiting approval\n"
            "   - Oldest open POs\n"
        )

    @mcp.prompt()
    async def grant_status_check() -> str:
        """
        Check grant status and spending — budget vs actuals, remaining
        balance, and compliance timeline for a specific grant.
        """
        return (
            "You are reviewing grant status. Follow these steps:\n\n"
            "1. Get grant master details:\n"
            '   - Use query_business_class or find_record with business_class="GrantMaster"\n'
            "   - Get grant name, funding agency, start/end dates, total award\n\n"
            "2. Query grant budget records:\n"
            '   - business_class="GrantBudget"\n'
            "   - Get budget by category/line item\n\n"
            "3. Query GL actuals charged to the grant:\n"
            '   - business_class="GeneralLedgerDetail"\n'
            "   - Filter by the grant's project or accounting unit\n\n"
            "4. Summarize:\n"
            "   - Total award vs total spent vs remaining\n"
            "   - Spend by budget category\n"
            "   - Burn rate and projected end date\n"
            "   - Any categories over budget\n"
            "   - Upcoming compliance deadlines\n"
        )

    @mcp.prompt()
    async def gl_variance_analysis() -> str:
        """
        Analyze why a GL account or expense category changed between periods.
        Identifies the top drivers, new transactions, and provides an explanation.
        """
        return (
            "The user is asking about a change in a GL account or expense category "
            "between two periods. Use the analyze_gl_variance tool to investigate.\n\n"
            "1. Identify what the user is asking about:\n"
            "   - If they mention an account number, use the 'account' parameter\n"
            "   - If they mention a category name (e.g. 'utilities', 'travel'), use 'account_description'\n"
            "   - If they mention a department or fund, use 'accounting_unit'\n\n"
            "2. Identify the time periods:\n"
            "   - 'current_period' is the period they're asking about (YYYY-MM)\n"
            "   - 'comparison_period' is what to compare against (defaults to prior month)\n"
            "   - If they say 'this month' use the current month\n"
            "   - If they say 'vs last year' use same month prior year\n\n"
            "3. Call analyze_gl_variance with the parameters.\n\n"
            "4. Interpret the results for the user:\n"
            "   - Start with the headline: total change amount and percentage\n"
            "   - Explain the top 2-3 drivers by source code and vendor\n"
            "   - Highlight any new transactions not present in the comparison period\n"
            "   - Note any transactions that stopped (present in comparison but not current)\n"
            "   - If a single vendor or transaction dominates the variance, call it out\n"
            "   - Offer to drill deeper into specific drivers if needed\n\n"
            "5. Keep the explanation conversational and actionable — the user is a finance\n"
            "   professional who wants to understand WHY, not just WHAT.\n"
        )

    @mcp.prompt()
    async def month_end_close_checklist() -> str:
        """
        Month-end close status check — verify key close tasks,
        check for open items, and identify blocking issues.
        """
        return (
            "You are helping with month-end close. Check the following:\n\n"
            "1. Open AP invoices that should be accrued:\n"
            '   - query_business_class: APInvoice, filter for open/unposted invoices\n\n'
            "2. Unposted journal entries:\n"
            '   - query_business_class: JournalEntry, filter for unposted status\n\n'
            "3. Bank reconciliation status:\n"
            '   - query_business_class: BankReconciliation for the closing period\n\n'
            "4. Open purchase orders with receipts not vouchered:\n"
            '   - query_business_class: PurchaseOrder, look for received but not invoiced\n\n'
            "5. Summarize:\n"
            "   - List of blocking items by category\n"
            "   - Estimated dollar impact of each open item\n"
            "   - Recommended priority for resolution\n"
            "   - Note any items that may require management approval\n"
        )

    logger.info("Registered MCP prompts")
