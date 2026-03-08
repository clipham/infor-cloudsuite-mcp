"""
Phase 1 Read-Only Query Tools for Infor CloudSuite MCP Server

These tools provide natural language access to Landmark business class data
through the ION API Gateway's V2 REST (Raw Data Web Services) API.

Tool Design Principles:
- Each tool maps to a specific Landmark REST API pattern
- Tool descriptions include enough context for the LLM to construct valid queries
- Parameters are typed and documented so the LLM knows what to pass
- Responses are formatted for readability, not raw API dumps
- Error messages include actionable hints for the LLM to self-correct
"""

import json
import logging
from typing import Optional

from infor_mcp.client import IONClient

logger = logging.getLogger("infor_mcp.tools.query")


def register_query_tools(mcp, client: IONClient):
    """Register all Phase 1 read-only query tools with the MCP server."""

    @mcp.tool()
    async def query_business_class(
        business_class: str,
        fields: str = "_all",
        filter_expr: str = "",
        limit: int = 20,
        set_name: str = "SymbolicKey",
    ) -> str:
        """
        Query records from any Infor CloudSuite Landmark business class.

        Returns matching records with the specified fields. Use this to search,
        list, and analyze data across the entire CloudSuite system.

        Args:
            business_class: The Landmark business class name. Examples:
                - Finance: APInvoice, APVoucherInvoice, ARInvoice, GeneralLedgerDetail,
                  CashLedgerPayment, BankStatement, ChartOfAccounts, Budget
                - Purchasing: PurchaseOrder, PurchaseOrderLine, RequisitionHeader
                - Vendors: Vendor, VendorLocation, VendorGroup
                - Projects: ProjectMaster, ProjectBudget, ActivityMaster
                - Grants: GrantMaster, GrantBudget
                - Assets: AssetMaster, AssetBook
                - HR: Employee, Position, PayrollHistory
                Use list_business_classes to discover all available classes.
            fields: Comma-separated field names, or "_all" for all fields.
                Use dot notation for related fields: "Vendor.VendorName"
                Example: "InvoiceNumber,VendorName,InvoiceAmount,InvoiceDate,Status"
            filter_expr: Filter expression to narrow results. Format:
                "FieldName::Value" — single filter
                "Field1::Value1|Field2::Value2" — multiple filters (AND)
                Examples:
                    "Status::Open"
                    "VendorGroup::1000|Status::Open"
                    "InvoiceDate::2024-01-01"
                Leave empty to return all records up to the limit.
            limit: Maximum number of records to return (1-100). Default 20.
            set_name: The business class set to query. Usually "SymbolicKey" (default).
                Some classes have alternative sets for different field groupings.

        Returns:
            JSON array of matching records with the requested fields.
        """
        # Clamp limit to safe range
        limit = max(1, min(limit, 100))

        params = {
            "_setName": set_name,
            "_fields": fields,
            "_limit": str(limit),
            "_links": "true",
        }

        if filter_expr:
            # Landmark expects the filter wrapped in quotes
            params["_filter"] = f'"{filter_expr}"'

        path = f"/soap/classes/{business_class}/lists/_generic"
        logger.info(f"Querying {business_class} (fields={fields}, filter={filter_expr}, limit={limit})")
        result = await client.get(path, params)
        return result

    @mcp.tool()
    async def find_record(
        business_class: str,
        key_values: str,
    ) -> str:
        """
        Find a specific record by its key field values.

        Use this when you know the exact identifier of a record and want
        to retrieve its full details, including available actions and related links.

        Args:
            business_class: The Landmark business class name (e.g. "APInvoice", "Vendor")
            key_values: Key field values as "Field1=Value1&Field2=Value2" format.
                The key fields vary by business class. Common examples:
                    APInvoice: "Company=1&InvoiceNumber=INV-001"
                    Vendor: "Vendor=V001"
                    PurchaseOrder: "Company=1&PurchaseOrder=PO-2024-001"
                    Employee: "Employee=EMP001"
                    GeneralLedgerDetail: "Company=1&AccountingUnit=100&Account=6200"
                Use list_business_class_details to discover key fields for a class.

        Returns:
            JSON object with full record details and available actions/links.
        """
        # Parse the key_values string into a dict
        params = {"_links": "true"}
        if key_values:
            for pair in key_values.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k.strip()] = v.strip()

        path = f"/soap/classes/{business_class}/actions/Find"
        logger.info(f"Finding {business_class} record: {key_values}")
        result = await client.get(path, params)
        return result

    @mcp.tool()
    async def list_business_classes(
        search_term: str = "",
    ) -> str:
        """
        Discover available Landmark business classes in this CloudSuite environment.

        Use this to find the correct business class name before querying.
        Business classes represent the core data entities in CloudSuite —
        invoices, vendors, purchase orders, GL accounts, employees, etc.

        Args:
            search_term: Optional filter to narrow results. Examples:
                "Invoice" — find all invoice-related classes
                "Vendor" — find vendor/supplier classes
                "GL" or "GeneralLedger" — find general ledger classes
                "Project" — find project accounting classes
                "Grant" — find grant management classes
                "Asset" — find fixed asset classes
                "" (empty) — list all available classes

        Returns:
            List of matching business class names with links to their
            available actions, sets, and field definitions.
        """
        if search_term:
            path = f"/soap/classes/{search_term}"
        else:
            path = "/soap/classes"

        params = {"_links": "true"}
        logger.info(f"Listing business classes (search: '{search_term}')")
        result = await client.get(path, params)
        return result

    @mcp.tool()
    async def list_business_class_details(
        business_class: str,
    ) -> str:
        """
        Get detailed metadata for a specific business class — its fields,
        sets, and available actions.

        Use this to understand the structure of a business class before
        querying it. Returns field names, types, and available operations
        (Create, Update, Delete, Find, etc.)

        Args:
            business_class: The exact business class name (e.g. "APInvoice")

        Returns:
            JSON with:
            - Available sets (field groupings) and their fields
            - Available actions (Create, Update, Delete, Find, etc.)
            - Links to related classes
        """
        # Get sets (field groupings)
        sets_path = f"/soap/classes/{business_class}/sets"
        sets_params = {"_links": "true"}

        logger.info(f"Getting metadata for {business_class}")
        sets_result = await client.get(sets_path, sets_params)

        return sets_result

    @mcp.tool()
    async def get_field_values(
        business_class: str,
        set_name: str = "SymbolicKey",
        field_name: str = "_all",
        limit: int = 50,
    ) -> str:
        """
        Get the fields and sample values from a business class set.

        Useful for understanding what data is available in a business class
        and what the field names are before constructing a query.

        Args:
            business_class: The business class name
            set_name: The set to inspect (default "SymbolicKey")
            field_name: Specific field to inspect, or "_all" for all fields
            limit: Number of sample records to return

        Returns:
            JSON with field definitions and sample values from the set.
        """
        path = f"/soap/classes/{business_class}/sets/{set_name}"
        params = {"_links": "true"}

        logger.info(f"Getting fields for {business_class}.{set_name}")
        result = await client.get(path, params)
        return result

    @mcp.tool()
    async def run_form_operation(
        business_class: str,
        operation: str,
        parameters: str = "",
    ) -> str:
        """
        Execute a read-only form operation on a business class.

        Form operations are pre-defined queries and actions exposed by Landmark.
        Use this for operations that go beyond simple list queries, like
        running a specific inquiry form or retrieving computed values.

        NOTE: This tool is currently limited to read-only operations (Find, Get, List).
        Write operations (Create, Update, Delete) will be available in Phase 2.

        Args:
            business_class: The business class name
            operation: The form operation name. Common patterns:
                - "Find_{FormName}_FormOperation" — Find a specific record
                - "{ListName}_ListOperation" — Run a predefined list/inquiry
                Discover available operations using list_business_class_details.
            parameters: Operation parameters as "Field1=Value1&Field2=Value2".
                The required parameters depend on the specific operation.

        Returns:
            JSON response from the form operation.
        """
        # Safety check — block write operations in Phase 1
        write_prefixes = ("Create_", "Update_", "Delete_", "Approve_", "Submit_", "Post_")
        if any(operation.startswith(prefix) for prefix in write_prefixes):
            return json.dumps({
                "error": True,
                "hint": (
                    "Write operations are not enabled in Phase 1 (read-only mode). "
                    "This tool currently only supports read/query form operations. "
                    "Write operations will be available when Phase 2 is deployed."
                ),
            }, indent=2)

        # Build params
        params = {"_links": "true"}
        if parameters:
            for pair in parameters.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k.strip()] = v.strip()

        path = f"/soap/ldrest/{business_class}/{operation}"
        logger.info(f"Running form operation: {business_class}/{operation}")
        result = await client.get(path, params)
        return result

    logger.info("Registered Phase 1 query tools")
