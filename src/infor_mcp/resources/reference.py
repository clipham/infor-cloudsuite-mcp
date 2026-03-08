"""
MCP Resources for Infor CloudSuite

Resources expose read-only reference data that the LLM can use for context
when answering questions. Unlike tools (which the model calls on demand),
resources are available as background context.

These resources provide the LLM with knowledge about common CloudSuite
data structures so it can construct better queries without trial and error.
"""

import json
import logging

logger = logging.getLogger("infor_mcp.resources")

# Common Landmark business classes organized by functional area
# This helps the LLM pick the right class without calling list_business_classes every time
COMMON_BUSINESS_CLASSES = {
    "Accounts Payable": {
        "APInvoice": "AP invoices (header level). Key: Company, InvoiceNumber",
        "APInvoiceLine": "AP invoice line items. Key: Company, InvoiceNumber, InvoiceLine",
        "APVoucherInvoice": "Vouchered AP invoices ready for payment",
        "APPayment": "AP payments / checks issued",
        "APRecurringInvoice": "Recurring AP invoice templates",
    },
    "Accounts Receivable": {
        "ARInvoice": "AR invoices / billings. Key: Company, ARInvoice",
        "ReceivableApplication": "Cash receipts applied to AR invoices",
        "ReceivableApplicationAdjustment": "AR adjustments, write-offs, refunds",
        "CashLedgerPayment": "Cash receipts / deposits",
    },
    "General Ledger": {
        "GeneralLedgerDetail": "GL transaction detail (journal entries). Key: Company, AccountingUnit, Account",
        "GeneralLedgerSummary": "GL account balances by period",
        "ChartOfAccounts": "Chart of accounts / account master",
        "AccountingUnit": "Accounting units (departments, funds, cost centers)",
        "JournalEntry": "Manual journal entries",
        "Budget": "Budget records by account and period",
    },
    "Purchasing": {
        "PurchaseOrder": "Purchase order headers. Key: Company, PurchaseOrder",
        "PurchaseOrderLine": "PO line items",
        "RequisitionHeader": "Purchase requisition headers",
        "RequisitionLine": "Requisition line items",
        "BuyerMaster": "Buyer/purchasing agent master",
    },
    "Vendors / Suppliers": {
        "Vendor": "Vendor master. Key: Vendor",
        "VendorLocation": "Vendor addresses/locations",
        "VendorGroup": "Vendor classification groups",
    },
    "Cash Management": {
        "BankAccount": "Bank account master records",
        "BankStatement": "Imported bank statements",
        "BankReconciliation": "Bank reconciliation records",
        "CashForecast": "Cash flow forecasts",
    },
    "Projects & Grants": {
        "ProjectMaster": "Project master records. Key: Company, Project",
        "ProjectBudget": "Project budget records",
        "ActivityMaster": "Project activity/task master",
        "GrantMaster": "Grant master records",
        "GrantBudget": "Grant budget and allocation records",
    },
    "Assets": {
        "AssetMaster": "Fixed asset master records",
        "AssetBook": "Asset depreciation book records",
        "AssetTransaction": "Asset transactions (acquisitions, disposals)",
    },
    "Human Resources": {
        "Employee": "Employee master. Key: Employee",
        "Position": "Position/job master records",
        "PayrollHistory": "Historical payroll records",
    },
    "System": {
        "Company": "Company/entity master",
        "FiscalCalendar": "Fiscal year and period definitions",
        "CurrencyCode": "Currency master records",
    },
}

# Common filter patterns the LLM can reference
FILTER_PATTERNS = {
    "Single field equals": 'filter_expr="Status::Open"',
    "Multiple fields (AND)": 'filter_expr="Status::Open|VendorGroup::1000"',
    "Date field": 'filter_expr="InvoiceDate::2024-01-01"',
    "Numeric comparison": 'filter_expr="InvoiceAmount::5000"',
    "Note": (
        "Landmark filters use :: as the separator between field and value, "
        "and | between multiple filter conditions. Filters are always AND logic. "
        "For complex queries, use run_form_operation with a predefined inquiry."
    ),
}


GL_ANALYSIS_PATTERNS = {
    "Expense variance (month over month)": {
        "tool": "analyze_gl_variance",
        "params": 'account_description="utilities", current_period="2026-03", comparison_period="2026-02"',
    },
    "Expense variance (year over year)": {
        "tool": "analyze_gl_variance",
        "params": 'account="6200", current_period="2026-03", comparison_period="2025-03"',
    },
    "Department-specific variance": {
        "tool": "analyze_gl_variance",
        "params": 'account="5100", accounting_unit="4200", current_period="2026-03"',
    },
    "Note": (
        "The analyze_gl_variance tool handles the full workflow: account resolution, "
        "GL detail pull for both periods, variance computation, and driver identification. "
        "Use account_description for natural language account names (e.g. 'utilities', 'travel') "
        "or account for exact account numbers. If comparison_period is omitted, "
        "it defaults to the month before current_period."
    ),
}


def register_resources(mcp):
    """Register MCP resources for CloudSuite reference data."""

    @mcp.resource("infor://reference/business-classes")
    async def get_business_classes_reference() -> str:
        """
        Reference guide to common Infor CloudSuite Landmark business classes,
        organized by functional area. Use this to identify the correct business
        class name for queries.
        """
        return json.dumps(COMMON_BUSINESS_CLASSES, indent=2)

    @mcp.resource("infor://reference/filter-patterns")
    async def get_filter_patterns() -> str:
        """
        Reference guide to Landmark REST API filter syntax.
        Use this to construct valid filter expressions for query_business_class.
        """
        return json.dumps(FILTER_PATTERNS, indent=2)

    @mcp.resource("infor://reference/api-patterns")
    async def get_api_patterns() -> str:
        """
        Reference guide to Landmark V2 REST API URL patterns.
        Explains the API conventions used by the query tools.
        """
        return json.dumps({
            "List query": "GET /soap/classes/{BusinessClass}/lists/_generic?_setName=SymbolicKey&_fields=_all&_limit=20",
            "Find record": "GET /soap/classes/{BusinessClass}/actions/Find?{KeyField}={Value}",
            "Discover classes": "GET /soap/classes?_links=true",
            "Class metadata": "GET /soap/classes/{BusinessClass}/sets?_links=true",
            "Form operation": "GET /soap/ldrest/{BusinessClass}/{OperationName}?{params}",
            "Batch operation": "POST /soap/classes/{BusinessClass}/actions/Find/batch",
            "Parameters": {
                "_fields": "Comma-separated fields or _all",
                "_limit": "Max records (default varies, max ~500)",
                "_filter": 'Filter expression in quotes: "Field::Value|Field2::Value2"',
                "_links": "Include hypermedia links (true/false)",
                "_setName": "Business class set name (usually SymbolicKey)",
                "_fts": "From timestamp for incremental queries (YYYYMMDDHHMMSSFF)",
            },
        }, indent=2)

    @mcp.resource("infor://reference/gl-analysis-patterns")
    async def get_gl_analysis_patterns() -> str:
        """
        Reference guide for GL variance analysis tool usage.
        Shows common patterns for period-over-period expense analysis.
        """
        return json.dumps(GL_ANALYSIS_PATTERNS, indent=2)

    logger.info("Registered MCP resources")
