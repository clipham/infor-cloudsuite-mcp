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
        "PayablesInvoice": "AP invoices (header level). Key: Company, InvoiceNumber, Vendor",
        "PayablesInvoiceDetail": "AP invoice line/distribution details",
        "PayablesInvoiceDistribution": "AP invoice GL distributions",
        "PayablesInvoiceHistory": "Historical/posted AP invoices",
        "PayablesCompany": "AP company configuration",
        "PayablesProcessLevel": "AP process level setup",
    },
    "Accounts Receivable": {
        "ReceivableInvoice": "AR invoices / billings",
        "ReceivableApplication": "Cash receipts applied to AR invoices",
        "ReceivableApplicationAdjustment": "AR adjustments, write-offs, refunds",
        "CashLedgerPayment": "Cash ledger payments / receipts",
        "ReceivableCompany": "AR company configuration",
        "CompanyCustomer": "Customer records by company",
    },
    "General Ledger": {
        "GLTransactionDetail": "Posted GL transactions. Account embedded in FinanceCodeBlock (pipe-delimited). Key fields: PostingDate (YYYYMMDD), TransactionAmount, Debit, Credit, System, Description",
        "GeneralLedgerTransaction": "GL transaction records (alternate view). Has ByAccount, ByPostingDate sort sets",
        "GeneralLedgerTotal": "GL account period balances/totals",
        "GeneralLedgerChartAccount": "Chart of accounts. Fields: GeneralLedgerChartAccount (acct#), AccountDescription",
        "AccountingUnit": "Accounting units (departments, funds, cost centers)",
        "GeneralLedgerJournalControl": "Journal entry headers",
        "GeneralLedgerCalendar": "GL fiscal calendar definitions",
        "GeneralLedgerCalendarPeriod": "GL fiscal period definitions",
        "GeneralLedgerCompany": "GL company master",
        "GeneralLedgerCode": "GL system/source codes",
        "BudgetGroup": "Budget group records",
        "BudgetGroupTotal": "Budget group totals by period",
    },
    "Purchasing": {
        "PurchaseOrder": "Purchase order headers",
        "PurchaseOrderLine": "PO line items",
        "PurchaseOrderReceipt": "PO receipt headers",
        "PurchaseOrderReceiptLine": "PO receipt line items",
        "Requisition": "Purchase requisition headers",
        "RequisitionLine": "Requisition line items",
        "Buyer": "Buyer/purchasing agent master",
        "PurchasingCompany": "Purchasing company configuration",
    },
    "Vendors / Suppliers": {
        "Vendor": "Vendor master",
        "VendorAddress": "Vendor addresses",
        "VendorLocation": "Vendor locations",
        "VendorClass": "Vendor classification",
        "PurchasingVendor": "Purchasing-specific vendor data",
    },
    "Cash Management": {
        "CashManagementAccount": "Bank/cash account master records",
        "CashCode": "Cash code definitions",
        "BankStatement": "Imported bank statements",
        "BankStatementLine": "Bank statement line items",
        "BankStatementReconciliation": "Bank reconciliation records",
        "CashLedgerTransaction": "Cash ledger transactions",
        "CashForecast": "Cash flow forecasts",
    },
    "Projects & Grants": {
        "Project": "Project master records",
        "ProjectFundingSource": "Project/grant funding sources",
        "ProjectContract": "Project contracts",
        "ProjectAssignment": "Project assignments/tasks",
        "GrantReportingSettings": "Grant reporting configuration",
    },
    "Assets": {
        "Asset": "Fixed asset master records",
        "AssetBook": "Asset depreciation book records",
        "AssetBookHistory": "Asset book period history",
        "AssetTransaction": "Asset transactions (acquisitions, disposals, transfers)",
        "AssetCompany": "Asset company configuration",
    },
    "Human Resources": {
        "Employee": "Employee master",
        "EmployeeAddress": "Employee address records",
        "Position": "Position/job master records",
        "HROrganization": "HR organization structure",
    },
    "System": {
        "FinanceCompany": "Finance company master",
        "FinanceEnterpriseGroup": "Finance enterprise group configuration",
        "FinancePeriod": "Finance period definitions",
        "AccountingEntity": "Accounting entity master",
        "Currency": "Currency master records",
        "CurrencyTable": "Currency exchange rate tables",
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
