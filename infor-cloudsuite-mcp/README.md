# Infor CloudSuite MCP Server

The first MCP (Model Context Protocol) server for Infor CloudSuite — an AI-powered orchestration layer that gives LLMs structured access to Landmark business class data through the ION API Gateway.

Ask your ERP questions in plain English. Get answers from live data.

## What This Does

Connect Claude (or any MCP-compatible AI) directly to your Infor CloudSuite environment. Query invoices, vendors, GL accounts, purchase orders, grants, and any other Landmark business class through natural language.

**Example prompts:**
- "Show me all open AP invoices over $10,000"
- "Why did utilities increase in March?"
- "What's the YTD spend in account 6200 for Fund 100?"
- "Compare travel expenses between January and February"
- "List all vendors in vendor group 1000 with their outstanding balance"
- "Find purchase order PO-2024-0042 and show me its line items"
- "What drove the change in office supplies spending this quarter?"

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Access to an Infor CloudSuite environment with ION API Gateway
- Claude Desktop, Claude Code, or Claude.ai Pro plan

### 2. Install

```bash
git clone <your-repo-url>
cd infor-cloudsuite-mcp
pip install -e .
```

### 3. Configure ION API Credentials

This is the critical step — you need to create an Authorized App in ION API Gateway and download the credentials file.

#### Step A: Create the Authorized App

1. Log into your Infor CloudSuite environment
2. Navigate to **Infor OS > ION API** (or search for "ION API" in the hamburger menu)
3. Go to **Authorized Apps**
4. Click **+ Add** (or "Create New")
5. Configure:
   - **Name**: `InforMCP` (or any descriptive name)
   - **Type**: **Backend Service**
   - **Description**: "MCP Server for AI-powered CloudSuite access"
6. Save the application

#### Step B: Create a Service Account

1. Still in ION API, go to the **Service Accounts** section of your new app
2. Create a new service account
3. **Map it to a CloudSuite user** — this user determines what the MCP server can access
   - For Phase 1 (read-only), map to a user with inquiry-level access
   - The user should have access to the financial modules you want to query
   - **Do NOT map to an admin account** — use least-privilege
4. Note the service account access key and secret key

#### Step C: Download Credentials

1. In your Authorized App, click **Download Credentials**
2. This downloads a `.ionapi` file (JSON format)
3. Place it at `config/.ionapi` in the project directory

#### Step D: Set Environment Variables

```bash
cp .env.example .env
```

Edit `.env`:
```
IONAPI_PATH=config/.ionapi
INFOR_DATA_AREA=fsm
```

The `INFOR_DATA_AREA` depends on your CloudSuite product:
- `fsm` — CloudSuite Financials & Supply Management (most common)
- `hcm` — CloudSuite Human Capital Management
- `hrt` — CloudSuite HR Talent

### 4. Run with Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "infor-cloudsuite": {
      "command": "python",
      "args": ["-m", "infor_mcp.server"],
      "cwd": "/path/to/infor-cloudsuite-mcp",
      "env": {
        "IONAPI_PATH": "/path/to/infor-cloudsuite-mcp/config/.ionapi",
        "INFOR_DATA_AREA": "fsm"
      }
    }
  }
}
```

Restart Claude Desktop. You should see the Infor CloudSuite tools in the tool picker.

### 5. Run with Claude Code

```bash
cd infor-cloudsuite-mcp
claude mcp add infor-cloudsuite -- python -m infor_mcp.server
```

### 6. Test with MCP Inspector

```bash
mcp dev src/infor_mcp/server.py
```

This opens the MCP Inspector in your browser where you can test tools interactively.

## Available Tools (Phase 1)

| Tool | Description |
|------|-------------|
| `query_business_class` | Query any Landmark business class with field selection and filtering |
| `find_record` | Find a specific record by key fields |
| `list_business_classes` | Discover available business classes |
| `list_business_class_details` | Get field metadata for a business class |
| `get_field_values` | Inspect fields and sample values from a class |
| `run_form_operation` | Execute read-only form operations |
| `analyze_gl_variance` | Period-over-period GL variance analysis with driver identification |

## Available Prompts

| Prompt | Description |
|--------|-------------|
| `ap_aging_analysis` | Analyze AP aging buckets and payment priorities |
| `vendor_spend_analysis` | Analyze spending by vendor |
| `gl_account_inquiry` | Investigate GL account activity and budgets |
| `purchase_order_status` | Check PO status across the organization |
| `grant_status_check` | Review grant budget vs actuals |
| `gl_variance_analysis` | Analyze why a GL account changed between periods |
| `month_end_close_checklist` | Month-end close status and blocking items |

## Security Notes

- The `.ionapi` file contains OAuth credentials — **never commit it to git**
- The service account user determines access scope — use least-privilege
- All API calls are auditable through ION API Gateway logs
- Phase 1 is read-only — write operations are explicitly blocked
- The server never exposes credentials to the AI client

## Project Structure

```
infor-cloudsuite-mcp/
├── config/
│   └── .ionapi              # Your credentials (gitignored)
├── src/infor_mcp/
│   ├── __init__.py
│   ├── server.py            # MCP server entry point
│   ├── auth.py              # OAuth token manager
│   ├── client.py            # ION API HTTP client
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── query.py         # Phase 1 read-only tools
│   │   └── analysis.py      # GL variance analysis tools
│   ├── resources/
│   │   ├── __init__.py
│   │   └── reference.py     # Reference data for LLM context
│   └── prompts/
│       ├── __init__.py
│       └── workflows.py     # Pre-built workflow prompts
├── tests/
│   └── __init__.py
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

## Roadmap

- **Phase 1** (current): Read-only query tools — search, list, analyze
- **Phase 2**: Write operations — create records, update records, run actions (with confirmation gates)
- **Phase 3**: Agentic workflows — multi-step process execution, scheduled agents, autonomous operations

## License

MIT
