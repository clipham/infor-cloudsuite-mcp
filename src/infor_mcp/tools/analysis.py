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
"""

import json
import logging
from typing import Optional

from infor_mcp.client import IONClient

logger = logging.getLogger("infor_mcp.tools.analysis")


def register_analysis_tools(mcp, client: IONClient):
    """Register GL analysis tools with the MCP server."""

    @mcp.tool()
    async def analyze_gl_variance(
        account: str = "",
        account_description: str = "",
        accounting_unit: str = "",
        current_period: str = "",
        comparison_period: str = "",
        company: str = "1",
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
            account: GL account number (e.g. "6200", "5100"). Can be partial
                for prefix matching. Leave empty if using account_description.
            account_description: Search term for account name (e.g. "utilities",
                "travel", "office supplies"). Used to find the account if no
                number is provided.
            accounting_unit: Accounting unit / department / fund to filter by.
                Leave empty for all units.
            current_period: The period to analyze in YYYY-MM format (e.g. "2026-03").
                This is the period the user is asking about.
            comparison_period: The period to compare against in YYYY-MM format
                (e.g. "2026-02"). Usually the prior month or same month prior year.
                If empty, defaults to the month before current_period.
            company: Company/entity code. Default "1".
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
                        "(e.g. '2026-03'). I need to know which period you're asking about."
                    ),
                }, indent=2)

            # ── Step 1: Resolve account number from description if needed ──
            resolved_account = account
            account_name = account_description or account

            if not account and account_description:
                logger.info(f"Resolving account from description: '{account_description}'")
                acct_result = await _find_account_by_description(
                    client, company, account_description
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
                client, company, resolved_account, accounting_unit,
                current_period, limit
            )
            comparison_data = await _get_period_detail(
                client, company, resolved_account, accounting_unit,
                comparison_period, limit
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
            drivers_by_source = _group_variance(current_data, comparison_data, "source_code")
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
                    "by_source_code": _top_drivers(drivers_by_source, 5),
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


async def _find_account_by_description(
    client: IONClient,
    company: str,
    description: str,
) -> dict:
    """
    Search ChartOfAccounts for an account matching the description.
    Returns {"account": "6200", "description": "Utilities"} or an error dict.
    """
    # Try querying the chart of accounts
    path = "/soap/classes/ChartOfAccounts/lists/_generic"
    params = {
        "_setName": "SymbolicKey",
        "_fields": "Account,Description,AccountGroup,AccountType",
        "_limit": "20",
        "_links": "false",
    }

    result_text = await client.get(path, params)

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        return {"error": True, "hint": f"Could not parse account lookup response."}

    # Handle API error responses
    if isinstance(result, dict) and result.get("error"):
        return result

    # Search through results for matching description
    search_lower = description.lower()
    matches = []

    records = result if isinstance(result, list) else result.get("items", result.get("data", []))

    if isinstance(records, dict):
        # Single record returned
        records = [records]

    for record in records:
        desc = str(record.get("Description", record.get("description", ""))).lower()
        acct = str(record.get("Account", record.get("account", "")))
        if search_lower in desc:
            matches.append({
                "account": acct,
                "description": record.get("Description", record.get("description", "")),
            })

    if not matches:
        return {
            "error": True,
            "hint": (
                f"No GL account found matching '{description}'. "
                "Try using the exact account number, or use list_business_classes "
                "and query_business_class to explore available accounts."
            ),
        }

    # Return best match (first one found)
    return matches[0]


async def _get_period_detail(
    client: IONClient,
    company: str,
    account: str,
    accounting_unit: str,
    period: str,
    limit: int,
) -> list[dict]:
    """
    Pull GL transaction detail for a specific account and period.
    Returns a normalized list of transaction dicts.
    """
    # Build filter
    filter_parts = [f"Account::{account}"]
    if accounting_unit:
        filter_parts.append(f"AccountingUnit::{accounting_unit}")

    # Add period filter — Landmark stores fiscal period differently depending
    # on the class. We try PostingDate-based filtering and FiscalPeriod.
    # The period format YYYY-MM needs to map to the Landmark date format.
    if period:
        filter_parts.append(f"FiscalPeriod::{period}")

    filter_expr = "|".join(filter_parts)

    path = "/soap/classes/GeneralLedgerDetail/lists/_generic"
    params = {
        "_setName": "SymbolicKey",
        "_fields": (
            "Account,AccountingUnit,FiscalPeriod,PostingDate,"
            "Amount,Description,SourceCode,Vendor,VendorName,"
            "DocumentNumber,JournalEntryNumber,Company"
        ),
        "_filter": f'"{filter_expr}"',
        "_limit": str(min(limit, 100)),
        "_links": "false",
    }

    result_text = await client.get(path, params)

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        logger.warning(f"Could not parse GL detail response for period {period}")
        return []

    # Handle error responses
    if isinstance(result, dict) and result.get("error"):
        logger.warning(f"GL detail query error for period {period}: {result.get('hint')}")
        return []

    # Normalize the response into a consistent list of dicts
    records = result if isinstance(result, list) else result.get("items", result.get("data", []))
    if isinstance(records, dict):
        records = [records]

    normalized = []
    for r in records:
        normalized.append({
            "account": _get_field(r, "Account"),
            "accounting_unit": _get_field(r, "AccountingUnit"),
            "fiscal_period": _get_field(r, "FiscalPeriod"),
            "posting_date": _get_field(r, "PostingDate"),
            "amount": _to_float(_get_field(r, "Amount")),
            "description": _get_field(r, "Description"),
            "source_code": _get_field(r, "SourceCode"),
            "vendor": _get_field(r, "Vendor"),
            "vendor_name": _get_field(r, "VendorName"),
            "document_number": _get_field(r, "DocumentNumber"),
            "journal_entry": _get_field(r, "JournalEntryNumber"),
        })

    return normalized


def _get_field(record: dict, field_name: str, default: str = "") -> str:
    """
    Extract a field value from a Landmark API response record.
    Handles both PascalCase and lowercase key variations.
    """
    return str(
        record.get(field_name, record.get(field_name.lower(), default))
    )


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
        "JE": {"current": 1000, "comparison": 2000, "variance": -1000},
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
        sig = (txn.get("source_code", ""), txn.get("description", ""))
        b_signatures.add(sig)

    unique = []
    for txn in period_a:
        sig = (txn.get("source_code", ""), txn.get("description", ""))
        if sig not in b_signatures:
            unique.append(txn)

    # Sort by absolute amount descending
    unique.sort(key=lambda x: abs(x.get("amount", 0)), reverse=True)
    return unique
