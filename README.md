# workiva-mcp-sc

A local MCP server that lets Claude Desktop work with a Workiva workspace through Workiva's public Platform API. Unofficial, unsupported, use at your own risk.

## What it does
Read documents, spreadsheets, sheets, ranges, files, sections, and rich text. Look up workspace users. Manage tasks. Edit rich text and tables. Create anchors and destination links. Publish spreadsheet links.

Write-capable tools: `create_task`, `update_task`, `delete_task`, `submit_task_action`, `edit_rich_text`, `edit_table_cells`, `create_table`, `create_table_anchor`, `insert_rich_text_destination_link`, `insert_table_destination_link`, `publish_spreadsheet_links`. Set `WORKIVA_READ_ONLY=true` to disable all of them.

## Prerequisites
- Claude Desktop
- Python 3.10+ and [uv](https://docs.astral.sh/uv/) (or plain pip)
- Your own Workiva API client ID and secret (create a client in the Workiva developer console). Each user should use their own credentials so access matches their permissions.
- Scopes on the grant: read scopes for documents, spreadsheets, files and tasks. Add `file:write` and `task:write` for editing, and `organization:read` for user lookup.

## Install
```bash
git clone https://github.com/davidtaylor-wk/workiva-mcp-sc.git
cd workiva-mcp-sc
```

### 1. Test your credentials
```bash
uv run workiva_server.py --check
```
Without uv: `python3 -m pip install -r requirements.txt && python3 workiva_server.py --check`. The check prompts for your credentials, stores nothing, and lists up to 3 documents.

### 2. Add to Claude Desktop
Edit the config file:
- Mac: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Merge in the block from `claude_desktop_config.example.json` and replace the placeholder path and values. If the file already has an `mcpServers` section, add `workiva` alongside the existing entries.

Fully quit and reopen Claude Desktop.

## Environment variables
| Variable | Required | Purpose |
|---|---|---|
| `WORKIVA_CLIENT_ID` | yes | API client ID |
| `WORKIVA_CLIENT_SECRET` | yes | API client secret |
| `WORKIVA_ORG_ID` | for user lookup | Organization ID from the Org Admin UI |
| `WORKIVA_WORKSPACE_ID` | for user lookup | The `w_...` value in a document or task source URL |
| `WORKIVA_SCOPES` | no | Space-separated scopes to request |
| `WORKIVA_READ_ONLY` | no | `true` disables write tools |
| `WORKIVA_IAM_BASE_URL` / `WORKIVA_PLATFORM_BASE_URL` | no | Override API hosts |
| `WORKIVA_API_VERSION` | no | Defaults to `2026-01-01` |

## Troubleshooting
- Tools do not appear: fully quit Claude Desktop (not just close the window) and reopen.
- Logs (Mac): `~/Library/Logs/Claude/mcp*.log`
- 401: wrong client ID or secret. 403: the grant is missing a scope. 404: wrong platform base URL. 429: rate limited, retry shortly.
- `uv` not found by Claude Desktop: use the full path from `which uv` as `command`.

## Data handling
Credentials stay on your machine in your Claude Desktop config. Content returned by tools is sent to Claude as part of your conversation. Follow your organization's policies on what workspace content may be shared with an AI service.
