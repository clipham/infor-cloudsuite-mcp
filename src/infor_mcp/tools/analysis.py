"""
GL Variance Analysis Tools for Infor CloudSuite MCP Server

These tools go beyond simple queries — they orchestrate multiple API calls,
compute deltas, and return structured explanations of financial variances.

This is the bridge between Tier 1 (read-only queries) and Tier 2 (write ops).
The tool reads data, reasons about it, and presents findings — but doesn't
modify anything in the system.

Design note: We do the heavy lifting server-side rather than relying on the LLM
to chain multiple tool calls correctly. This gives us:
- Reliable multi-step execution (no LLM reasoning errors mid-chain)
- Consistent output format
- Better error handling across the API call sequence
- Faster execution (parallel API calls where possible)

Schema reference (from live FSM sandbox):
- GeneralLedgerChartAccount: account master — fields are GeneralLedgerChartAccount
  (account number) and AccountDescription
- GLTransactionDetail: posted GL transactions — account is embedded in
  FinanceCodeBlock (pipe-delimited: Ledger|Entity||Account|dims...), with
  PostingDate (YYYYMMDD), TransactionAmount, Debit, Credit, System (source),
  Description, Reference, DocumentNumber, AccountingEntity, VendorGroupAndVendor
- All API responses wrap field data inside a _fields object
"""

import json
import logging
from typing import Optional

from infor_mcp.client import IONClient

logger = logging.getLogger("infor_mcp.tools.analysis")


# ─── FinanceCodeBlock positions ─────────────────────────────────────────────
# The FinanceCodeBlock is pipe-delimited:
# Ledger|AccountingEntity|AccountingUnit|Account|Dim1|Dim2|...|Flag|Bool
FCB_LEDGER = 0
FCB_ENTITY = 1
FCB_ACCT_UNIT = 2
FCB_ACCOUNT = 3
# Positions 4+ are finance dimensions


def register_analysis_tools(mcp, client: IONClient):
    """Register GL analysis tools with the MCP server."""

    @mcp.tool()
    async def analyze_gl_variance(
        account: str = "",
        account_description: str = "",
        accounting_unit: str = "",
        current_period: str = "",
        comparison_period: str = "",
        accounting_entity: str = "",
        limit: int = 100,
    ) -> str:
        """
        Analyze why a GL account balance changed between two periods.

        Pulls transaction detail for both periods, computes the variance,
        and identifies the top drivers of the change — grouped by source,
        vendor, description, and accounting unit.

        Use this when a user asks questions like:
        - "Why did utilities increase in March?"
        - "What drove the change in travel expenses this quarter?"
        - "Compare office supplies spending between January and February"
        - "Why is account 6200 over budget?"

        Args:
            account: GL account number (e.g. "23590", "10100"). Can be partial
                for prefix matching. Leave empty if using account_description.
            account_description: Search term for account name (e.g. "utilities",
                "travel", "cash"). Used to find the account if no number provided.
            accounting_unit: Accounting unit / department to filter by.
                Leave empty for all units.
            current_period: The period to analyze in YYYY-MM format (e.g. "2026-02").
                This is the period the user is asking about.
            comparison_period: The period to compare against in YYYY-MM format
                (e.g. "2026-01"). Usually the prior month or same month prior year.
                If empty, defaults to the month before current_period.
            accounting_entity: Accounting entity code (e.g. "60"). If empty,
                returns transactions across all entities.
            limit: Max transactions per period to analyze. Default 100.

        Returns:
            Structured variance analysis with:
            - Period totals and net change
            - Top drivers grouped by source, vendor, and description
            - Individual transaction details for the largest variances
        """
        try:
            # ── Step 0: Resolve comparison period if not provided ──
            if current_period and not comparison_period:
                comparison_period = _prior_period(current_period)

            if not current_period:
                return json.dumps({
                    "error": True,
                    "hint": (
                        "Please specify the current_period in YYYY-MM format "
                        "(e.g. '2026-02'). I need to know which period you're asking about."
                    ),
                }, indent=2)

            # ── Step 1: Resolve account number from description if needed ──
            resolved_account = account
            account_name = account_description or account

            if not account and account_description:
                logger.info(f"Resolving account from description: '{account_description}'")
                acct_result = await _find_account_by_description(
                    client, account_description
                )
                if acct_result.get("error"):
                    return json.dumps(acct_result, indent=2)
                resolved_account = acct_result["account"]
                account_name = acct_result.get("description", account_description)

            if not resolved_account:
                return json.dumps({
                    "error": True,
                    "hint": (
                        "Please specify either an account number or an account description "
                        "so I can identify which GL account to analyze."
                    ),
                }, indent=2)

            # ── Step 2: Pull GL detail for both periods ──
            logger.info(
                f"Analyzing GL variance: account={resolved_account}, "
                f"current={current_period}, comparison={comparison_period}"
            )

            current_data = await _get_period_detail(
                client, resolved_account, accounting_unit,
                accounting_entity, current_period, limit
            )
            comparison_data = await _get_period_detail(
                client, resolved_account, accounting_unit,
                accounting_entity, comparison_period, limit
            )

            # ── Step 3: Compute variance ──
            current_total = sum(t.get("amount", 0) for t in current_data)
            comparison_total = sum(t.get("amount", 0) for t in comparison_data)
            variance = current_total - comparison_total

            if comparison_total != 0:
                pct_change = (variance / abs(comparison_total)) * 100
            else:
                pct_change = 100.0 if current_total != 0 else 0.0

            # ── Step 4: Identify drivers ──
            drivers_by_source = _group_variance(current_data, comparison_data, "system")
            drivers_by_vendor = _group_variance(current_data, comparison_data, "vendor")
            drivers_by_desc = _group_variance(current_data, comparison_data, "description")
            drivers_by_unit = _group_variance(current_data, comparison_data, "accounting_unit")

            # ── Step 5: Find new/missing transactions ──
            new_in_current = _find_unique_transactions(current_data, comparison_data)
            missing_from_current = _find_unique_transactions(comparison_data, current_data)

            # ── Step 6: Build the analysis response ──
            analysis = {
                "summary": {
                    "account": resolved_account,
                    "account_name": account_name,
                    "current_period": current_period,
                    "comparison_period": comparison_period,
                    "current_total": round(current_total, 2),
                    "comparison_total": round(comparison_total, 2),
                    "variance": round(variance, 2),
                    "pct_change": round(pct_change, 1),
                    "direction": "increase" if variance > 0 else "decrease" if variance < 0 else "unchanged",
                    "current_txn_count": len(current_data),
                    "comparison_txn_count": len(comparison_data),
                },
                "drivers": {
                    "by_source_system": _top_drivers(drivers_by_source, 5),
                    "by_vendor": _top_drivers(drivers_by_vendor, 5),
                    "by_description": _top_drivers(drivers_by_desc, 5),
                    "by_accounting_unit": _top_drivers(drivers_by_unit, 5),
                },
                "notable_items": {
                    "new_in_current_period": new_in_current[:5],
                    "not_in_current_period": missing_from_current[:5],
                },
                "current_period_detail": sorted(
                    current_data, key=lambda x: abs(x.get("amount", 0)), reverse=True
                )[:10],
                "comparison_period_detail": sorted(
                    comparison_data, key=lambda x: abs(x.get("amount", 0)), reverse=True
                )[:10],
            }

            return json.dumps(analysis, indent=2, default=str)

        except Exception as e:
            logger.error(f"GL variance analysis failed: {e}", exc_info=True)
            return json.dumps({
                "error": True,
                "hint": f"Analysis failed: {str(e)}. Try specifying the account number directly.",
            }, indent=2)

    logger.info("Registered GL analysis tools")


# ─── Helper functions ────────────────────────────────────────────────────────


def _prior_period(period: str) -> str:
    """
    Get the prior month for a YYYY-MM period string.
    "2026-03" → "2026-02", "2026-01" → "2025-12"
    """
    try:
        year, month = period.split("-")
        year, month = int(year), int(month)
        if month == 1:
            return f"{year - 1}-12"
        return f"{year}-{month - 1:02d}"
    except (ValueError, AttributeError):
        return period


def _period_to_date_range(period: str) -> tuple[str, str]:
    """
    Convert YYYY-MM to Landmark YYYYMMDD date range.
    "2026-02" → ("20260201", "20260228")
    "2026-01" → ("20260101", "20260131")
    """
    import calendar
    try:
        year, month = period.split("-")
        year, month = int(year), int(month)
        last_day = calendar.monthrange(year, month)[1]
        return f"{year}{month:02d}01", f"{year}{month:02d}{last_day:02d}"
    except (ValueError, AttributeError):
        return "00000000", "99999999"


def _parse_finance_code_block(fcb: str) -> dict:
    """
    Parse FinanceCodeBlock pipe-delimited string into components.
    Example: "CORE|60||23590||||||||||||0|false"
    Returns: {"ledger": "CORE", "entity": "60", "accounting_unit": "", "account": "23590"}
    """
    parts = fcb.split("|") if fcb else []
    return {
        "ledger": parts[FCB_LEDGER] if len(parts) > FCB_LEDGER else "",
        "entity": parts[FCB_ENTITY] if len(parts) > FCB_ENTITY else "",
        "accounting_unit": parts[FCB_ACCT_UNIT] if len(parts) > FCB_ACCT_UNIT else "",
        "account": parts[FCB_ACCOUNT] if len(parts) > FCB_ACCOUNT else "",
    }


def _extract_fields(record: dict) -> dict:
    """
    Extract field data from a Landmark API response record.
    Handles the _fields wrapper that the API uses.
    """
    if "_fields" in record:
        return record["_fields"]
    return record


async def _find_account_by_description(
    client: IONClient,
    description: str,
) -> dict:
    """
    Search GeneralLedgerChartAccount for an account matching the description.
    Returns {"account": "23590", "description": "Due To Other Funds"} or error dict.

    Live schema:
    - Business class: GeneralLedgerChartAccount
    - Key field: GeneralLedgerChartAccount (account number)
    - Description field: AccountDescription
    """
    path = "/soap/classes/GeneralLedgerChartAccount/lists/_generic"
    params = {
        "_setName": "SymbolicKey",
        "_fields": "GeneralLedgerChartAccount,AccountDescription",
        "_limit": "50",
        "_links": "false",
    }

    result_text = await client.get(path, params)

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        return {"error": True, "hint": "Could not parse account lookup response."}

    # Handle API error responses
    if isinstance(result, dict) and result.get("error"):
        return result

    # Ensure we have a list of records
    records = result if isinstance(result, list) else result.get("items", result.get("data", []))
    if isinstance(records, dict):
        records = [records]

    # Search through results for matching description
    search_lower = description.lower()
    matches = []

    for record in records:
        fields = _extract_fields(record)
        acct = str(fields.get("GeneralLedgerChartAccount", ""))
        desc = str(fields.get("AccountDescription", ""))

        if search_lower in desc.lower():
            matches.append({
                "account": acct,
                "description": desc,
            })

    if not matches:
        return {
            "error": True,
            "hint": (
                f"No GL account found matching '{description}'. "
                "Try using the exact account number, or use query_business_class "
                "on GeneralLedgerChartAccount to explore available accounts."
            ),
        }

    # Return best match (first one found)
    return matches[0]


async def _get_period_detail(
    client: IONClient,
    account: str,
    accounting_unit: str,
    accounting_entity: str,
    period: str,
    limit: int,
) -> list[dict]:
    """
    Pull GL transaction detail for a specific account and period.
    Returns a normalized list of transaction dicts.

    Strategy: Landmark REST API only supports equality filters (field::value),
    not range operators. We query with minimal server-side filters and do
    date range + account matching client-side.

    The ByPostingDate sort set orders oldest-first, so we use SymbolicKey
    with the filter to avoid pagination issues. If AccountingEntity is
    provided, it narrows the server-side result set significantly.
    """
    # Convert YYYY-MM to YYYYMMDD range for client-side date filtering
    start_date, end_date = _period_to_date_range(period)

    # Request key fields
    fields = (
        "GLTransactionDetail,"
        "FinanceCodeBlock,"
        "AccountingEntity,"
        "PostingDate,"
        "TransactionDate,"
        "TransactionAmount,"
        "Debit,"
        "Credit,"
        "System,"
        "GeneralLedgerEvent,"
        "Description,"
        "Reference,"
        "DocumentNumber,"
        "ControlDocumentNumber,"
        "VendorGroupAndVendor,"
        "CurrencyCode,"
        "PrimaryLedger,"
        "DerivedFunctionalAmount"
    )

    path = "/soap/classes/GLTransactionDetail/lists/_generic"

    # Try multiple days in the period to find transactions.
    # Landmark supports PostingDate::YYYYMMDD exact match, so we query
    # individual dates. To keep API calls reasonable, sample key dates
    # first, then fill in remaining days if we find activity.
    all_normalized = []
    dates_queried = set()

    # Generate all dates in the period
    import calendar
    try:
        year, month = period.split("-")
        year, month = int(year), int(month)
        last_day = calendar.monthrange(year, month)[1]
        all_dates = [f"{year}{month:02d}{d:02d}" for d in range(1, last_day + 1)]
    except (ValueError, AttributeError):
        all_dates = []

    # Query each date (batch to stay within reasonable API call count)
    for date_str in all_dates:
        filter_parts = [f"PostingDate::{date_str}"]
        if accounting_entity:
            filter_parts.append(f"AccountingEntity::{accounting_entity}")
        filter_expr = "|".join(filter_parts)

        params = {
            "_setName": "SymbolicKey",
            "_fields": fields,
            "_filter": f'"{filter_expr}"',
            "_limit": str(min(limit, 100)),
            "_links": "false",
        }

        result_text = await client.get(path, params)
        dates_queried.add(date_str)

        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            continue

        if isinstance(result, dict) and result.get("error"):
            continue

        records = result if isinstance(result, list) else result.get("items", result.get("data", []))
        if isinstance(records, dict):
            records = [records]

        for r in records:
            if "_fields" not in r and "FinanceCodeBlock" not in r:
                continue

            fields_data = _extract_fields(r)
            fcb = fields_data.get("FinanceCodeBlock", "")
            code_block = _parse_finance_code_block(fcb)
            txn_account = code_block["account"]
            txn_acct_unit = code_block["accounting_unit"]

            # Client-side filter: match on account number
            if account and txn_account != account:
                if not txn_account.startswith(account):
                    continue

            # Client-side filter: match on accounting unit if specified
            if accounting_unit and txn_acct_unit != accounting_unit:
                continue

            # Parse vendor
            vendor_raw = fields_data.get("VendorGroupAndVendor", "^0")
            vendor_parts = vendor_raw.split("^") if vendor_raw else ["", "0"]
            vendor_group = vendor_parts[0] if len(vendor_parts) > 0 else ""
            vendor_num = vendor_parts[1] if len(vendor_parts) > 1 else "0"
            vendor_display = f"{vendor_group}/{vendor_num}" if vendor_group else vendor_num

            # Use TransactionAmount as primary, fall back to Debit - Credit
            amount = _to_float(fields_data.get("TransactionAmount", "0"))
            if amount == 0:
                debit = _to_float(fields_data.get("Debit", "0"))
                credit = _to_float(fields_data.get("Credit", "0"))
                amount = debit - credit

            all_normalized.append({
                "account": txn_account,
                "accounting_unit": txn_acct_unit,
                "accounting_entity": fields_data.get("AccountingEntity", ""),
                "posting_date": fields_data.get("PostingDate", ""),
                "transaction_date": fields_data.get("TransactionDate", ""),
                "amount": amount,
                "debit": _to_float(fields_data.get("Debit", "0")),
                "credit": _to_float(fields_data.get("Credit", "0")),
                "system": fields_data.get("System", ""),
                "gl_event": fields_data.get("GeneralLedgerEvent", ""),
                "description": fields_data.get("Description", ""),
                "reference": fields_data.get("Reference", ""),
                "document_number": fields_data.get("DocumentNumber", ""),
                "control_doc": fields_data.get("ControlDocumentNumber", ""),
                "vendor": vendor_display,
                "currency": fields_data.get("CurrencyCode", ""),
                "ledger": code_block["ledger"],
            })

    logger.info(
        f"Period {period}: queried {len(dates_queried)} dates, "
        f"found {len(all_normalized)} matching transactions"
    )

    return all_normalized


def _to_float(val) -> float:
    """Safely convert a value to float."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _group_variance(
    current: list[dict],
    comparison: list[dict],
    group_field: str,
) -> dict[str, dict]:
    """
    Group transactions by a field and compute the variance between periods.

    Returns dict like:
    {
        "AP": {"current": 5000, "comparison": 3000, "variance": 2000},
        "GL": {"current": 1000, "comparison": 2000, "variance": -1000},
    }
    """
    groups: dict[str, dict] = {}

    for txn in current:
        key = txn.get(group_field, "Unknown") or "Unknown"
        if key not in groups:
            groups[key] = {"current": 0.0, "comparison": 0.0, "variance": 0.0}
        groups[key]["current"] += txn.get("amount", 0)

    for txn in comparison:
        key = txn.get(group_field, "Unknown") or "Unknown"
        if key not in groups:
            groups[key] = {"current": 0.0, "comparison": 0.0, "variance": 0.0}
        groups[key]["comparison"] += txn.get("amount", 0)

    # Compute variance
    for key in groups:
        groups[key]["variance"] = round(
            groups[key]["current"] - groups[key]["comparison"], 2
        )
        groups[key]["current"] = round(groups[key]["current"], 2)
        groups[key]["comparison"] = round(groups[key]["comparison"], 2)

    return groups


def _top_drivers(groups: dict[str, dict], n: int) -> list[dict]:
    """
    Return the top N drivers sorted by absolute variance magnitude.
    Filters out zero-variance groups.
    """
    drivers = [
        {"name": k, **v}
        for k, v in groups.items()
        if v["variance"] != 0
    ]
    drivers.sort(key=lambda x: abs(x["variance"]), reverse=True)
    return drivers[:n]


def _find_unique_transactions(
    period_a: list[dict],
    period_b: list[dict],
) -> list[dict]:
    """
    Find transactions in period_a that have no matching description + source
    combination in period_b. These are "new" or "missing" items.
    """
    b_signatures = set()
    for txn in period_b:
        sig = (txn.get("system", ""), txn.get("description", ""))
        b_signatures.add(sig)

    unique = []
    for txn in period_a:
        sig = (txn.get("system", ""), txn.get("description", ""))
        if sig not in b_signatures:
            unique.append(txn)

    # Sort by absolute amount descending
    unique.sort(key=lambda x: abs(x.get("amount", 0)), reverse=True)
    return unique
