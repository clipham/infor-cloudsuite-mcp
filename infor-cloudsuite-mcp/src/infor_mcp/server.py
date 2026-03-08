"""
Infor CloudSuite MCP Server

The first MCP server for Infor CloudSuite — an AI-powered orchestration layer
that gives LLMs (Claude, GPT, etc.) structured access to Landmark business
class data through the ION API Gateway.

Phase 1: Read-only query tools for searching, listing, and analyzing
CloudSuite data via natural language.

Usage:
    # Claude Desktop (stdio transport)
    infor-mcp

    # Development / testing with MCP Inspector
    mcp dev src/infor_mcp/server.py

    # Remote HTTP/SSE (for Claude.ai web connector)
    uvicorn infor_mcp.server:app --host 0.0.0.0 --port 8080
"""

import os
import sys
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

from infor_mcp.auth import IONAuthManager, IONAuthError
from infor_mcp.client import IONClient
from infor_mcp.tools.query import register_query_tools
from infor_mcp.tools.analysis import register_analysis_tools
from infor_mcp.resources.reference import register_resources
from infor_mcp.prompts.workflows import register_prompts

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("infor_mcp")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Path to .ionapi credentials file
IONAPI_PATH = os.getenv(
    "IONAPI_PATH",
    str(Path(__file__).parent.parent.parent / "config" / ".ionapi"),
)

# Landmark data area (fsm, hcm, etc.)
DATA_AREA = os.getenv("INFOR_DATA_AREA", "fsm")

# Server metadata
SERVER_NAME = "Infor CloudSuite"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Server initialization
# ---------------------------------------------------------------------------

def create_server() -> FastMCP:
    """
    Create and configure the MCP server with all tools, resources, and prompts.

    The server initializes lazily — OAuth authentication happens on the first
    tool call, not at startup. This means the server starts fast and fails
    gracefully if credentials are missing.
    """
    mcp = FastMCP(
        SERVER_NAME,
        instructions=(
            "You are connected to an Infor CloudSuite ERP system through the ION API Gateway. "
            "You can query any Landmark business class to retrieve financial, purchasing, HR, "
            "and operational data. Use the available tools to search records, find specific "
            "documents, and analyze data across the system.\n\n"
            "Key tips:\n"
            "- Use list_business_classes to discover available data entities\n"
            "- Use list_business_class_details to understand a class's fields before querying\n"
            "- Use query_business_class for searching and listing records\n"
            "- Use find_record when you know the exact record key\n"
            "- Use analyze_gl_variance for period-over-period expense analysis\n"
            "- Check the infor://reference/business-classes resource for common class names\n"
            "- Filter syntax uses :: between field and value, | between conditions\n"
            "- Field names are PascalCase (e.g. InvoiceNumber, VendorName, AccountingUnit)\n"
            "- Related fields use dot notation (e.g. Vendor.VendorName)\n"
        ),
    )

    # Validate credentials file exists (warn, don't fail — allows inspection without creds)
    ionapi_path = Path(IONAPI_PATH)
    if not ionapi_path.exists():
        logger.warning(
            f"ION API credentials file not found at: {ionapi_path}\n"
            "The server will start but tool calls will fail until credentials are configured.\n"
            "See README.md for setup instructions."
        )
        # Register tools with a dummy client that will fail gracefully
        _register_with_placeholder(mcp, ionapi_path)
    else:
        try:
            auth = IONAuthManager(ionapi_path)
            client = IONClient(auth, data_area=DATA_AREA)
            register_query_tools(mcp, client)
            register_analysis_tools(mcp, client)
            logger.info(
                f"Configured for tenant: {auth.tenant_id}, "
                f"data area: {DATA_AREA}, "
                f"API base: {auth.base_url}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize ION API client: {e}")
            _register_with_placeholder(mcp, ionapi_path)

    # Resources and prompts don't need live credentials
    register_resources(mcp)
    register_prompts(mcp)

    logger.info(f"{SERVER_NAME} MCP Server v{SERVER_VERSION} initialized")
    return mcp


def _register_with_placeholder(mcp: FastMCP, ionapi_path: Path):
    """Register a placeholder tool that explains missing credentials."""

    @mcp.tool()
    async def query_business_class(**kwargs) -> str:
        """Query records from any Infor CloudSuite Landmark business class."""
        return (
            f"ERROR: ION API credentials not configured.\n\n"
            f"Expected credentials file at: {ionapi_path}\n\n"
            f"To set up:\n"
            f"1. In Infor OS, go to ION API > Authorized Apps\n"
            f"2. Create a new Backend Service type application\n"
            f"3. Create a service account and map it to a CloudSuite user\n"
            f"4. Download the .ionapi credentials file\n"
            f"5. Place it at: {ionapi_path}\n"
            f"6. Restart the MCP server\n\n"
            f"Or set the IONAPI_PATH environment variable to point to your .ionapi file."
        )

    @mcp.tool()
    async def find_record(**kwargs) -> str:
        """Find a specific record by its key field values."""
        return f"ERROR: ION API credentials not configured. See query_business_class for setup instructions."

    @mcp.tool()
    async def list_business_classes(**kwargs) -> str:
        """Discover available Landmark business classes."""
        return f"ERROR: ION API credentials not configured. See query_business_class for setup instructions."

    @mcp.tool()
    async def list_business_class_details(**kwargs) -> str:
        """Get detailed metadata for a specific business class."""
        return f"ERROR: ION API credentials not configured. See query_business_class for setup instructions."

    @mcp.tool()
    async def get_field_values(**kwargs) -> str:
        """Get fields and sample values from a business class set."""
        return f"ERROR: ION API credentials not configured. See query_business_class for setup instructions."

    @mcp.tool()
    async def run_form_operation(**kwargs) -> str:
        """Execute a read-only form operation on a business class."""
        return f"ERROR: ION API credentials not configured. See query_business_class for setup instructions."

    @mcp.tool()
    async def analyze_gl_variance(**kwargs) -> str:
        """Analyze why a GL account balance changed between two periods."""
        return f"ERROR: ION API credentials not configured. See query_business_class for setup instructions."

    logger.warning("Registered placeholder tools (no credentials)")


# Create the server instance
mcp = create_server()

# For uvicorn (HTTP/SSE transport for remote hosting)
app = mcp.sse_app()


def main():
    """Entry point for stdio transport (Claude Desktop, Claude Code)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
