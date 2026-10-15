"""
Workiva MCP server — single-file version.

Two ways to run it:

  1) Test your connection (safe, asks for your credentials, stores nothing):
         python3 workiva_server.py --check

  2) As the server Claude Desktop talks to (Claude Desktop starts this for you;
     it reads your credentials from environment variables):
         python3 workiva_server.py

Your client secret never leaves your machine. In --check mode you paste it at a
hidden prompt; for everyday use it lives in your Claude Desktop settings.
"""

import asyncio
import getpass
import os
import sys
import time

import httpx

DEFAULT_IAM = "https://api.app.wdesk.com"
DEFAULT_PLATFORM = "https://api.app.wdesk.com"
API_VERSION = os.environ.get("WORKIVA_API_VERSION", "2026-01-01")

# Load a local .env if one happens to exist; optional.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _col_letters_to_index(letters: str) -> int:
    """Convert column letters ('A', 'Z', 'AA', ...) to a 0-based index."""
    index = 0
    for ch in letters.upper():
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def _col_index_to_letters(index: int) -> str:
    """Convert a 0-based column index back to letters ('A', 'Z', 'AA', ...)."""
    index += 1
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _split_a1(ref: str) -> tuple[str, int]:
    """Split an A1-style cell reference like 'G18' into ('G', 18)."""
    letters = "".join(ch for ch in ref if ch.isalpha())
    digits = "".join(ch for ch in ref if ch.isdigit())
    return letters, int(digits)


class WorkivaError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Workiva API error {status_code}: {body[:600]}")
        self.status_code = status_code


class Workiva:
    """Minimal async client: handles the login token, retries, and paging."""

    def __init__(self, client_id, client_secret, iam_base, platform_base,
                 version=API_VERSION, scopes=""):
        self.client_id = client_id
        self.client_secret = client_secret
        self.iam_base = iam_base.rstrip("/")
        self.platform_base = platform_base.rstrip("/")
        self.version = version
        self.scopes = scopes
        self._http = httpx.AsyncClient(timeout=25)
        self._token = None
        self._expires_at = 0.0

    async def _get_token(self):
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.scopes:
            data["scope"] = self.scopes
        resp = await self._http.post(
            f"{self.iam_base}/oauth2/token",
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Version": self.version,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Token request failed ({resp.status_code}): {resp.text[:400]}"
            )
        payload = resp.json()
        self._token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
        self._expires_at = time.monotonic() + max(expires_in - 60, 30)
        return self._token

    async def request(self, method, path, params=None, json=None):
        url = path if path.startswith("http") else f"{self.platform_base}/{path.lstrip('/')}"
        for attempt in range(4):
            headers = {
                "Authorization": f"Bearer {await self._get_token()}",
                "X-Version": self.version,
                "Accept": "application/json",
            }
            if json is not None:
                headers["Content-Type"] = "application/json"
            resp = await self._http.request(
                method, url, params=params, json=json, headers=headers
            )
            if resp.status_code == 429 and attempt < 3:
                await asyncio.sleep(float(resp.headers.get("Retry-After", "2")))
                continue
            if resp.status_code >= 400:
                raise WorkivaError(resp.status_code, resp.text)
            return resp.json() if resp.content else None
        raise WorkivaError(429, "Exceeded retry budget on rate limit")

    async def list(self, path, max_items=50, params=None):
        out = []
        query = dict(params or {})
        url = None
        while True:
            if url:
                payload = await self.request("GET", url)
            else:
                payload = await self.request("GET", path, params=query)
            data = payload.get("data", []) if isinstance(payload, dict) else payload
            out.extend(data or [])
            if max_items and len(out) >= max_items:
                return out[:max_items]
            url = payload.get("@nextLink") if isinstance(payload, dict) else None
            if not url:
                return out

    async def aclose(self):
        await self._http.aclose()


# --------------------------------------------------------------------------- #
# Mode 1: connection check
# --------------------------------------------------------------------------- #
async def _run_check(cid, csec, iam, plat):
    w = Workiva(cid, csec, iam, plat)
    try:
        print("\n[1 of 2] Logging in to Workiva ...")
        try:
            await w._get_token()
            print("         OK - Workiva accepted your credentials.")
        except Exception as exc:
            print(f"         FAILED - {exc}")
            print("         Likely cause: a wrong client ID/secret, an expired "
                  "grant, or a wrong IAM base URL.")
            return
        print("[2 of 2] Reading up to 3 documents ...")
        try:
            docs = await w.list("/documents", max_items=3)
            print(f"         OK - {len(docs)} document(s) returned.")
            for d in docs:
                name = d.get("name") or d.get("title") or "(unnamed)"
                print(f"           - {d.get('id', '?')}: {name}")
            print("\n*** Success - the connection works. You're ready for the "
                  "next step. ***")
        except WorkivaError as exc:
            hints = {
                401: "Credentials rejected - re-check the client ID/secret.",
                403: "Logged in but not allowed - the grant is missing a scope.",
                404: "Endpoint not found - usually a wrong Platform base URL.",
                429: "Rate limited - wait a moment and run it again.",
            }
            print(f"         FAILED - {exc}")
            print(f"         Hint: {hints.get(exc.status_code, 'See the message above.')}")
    finally:
        await w.aclose()


def run_check():
    print("=== Workiva connection check ===")
    print("Nothing you type here is saved or sent anywhere except Workiva.\n")
    cid = input("Paste your client ID, then press Enter: ").strip()
    csec = getpass.getpass("Paste your client secret, then press Enter "
                           "(it stays hidden as you paste): ").strip()
    iam = input(f"\nIAM base URL [press Enter for {DEFAULT_IAM}]: ").strip() or DEFAULT_IAM
    plat = (input(f"Platform base URL [press Enter for {DEFAULT_PLATFORM}]: ").strip()
            or DEFAULT_PLATFORM)
    asyncio.run(_run_check(cid, csec, iam, plat))


# --------------------------------------------------------------------------- #
# Mode 2: the MCP server
# --------------------------------------------------------------------------- #
def run_server():
    try:
        from fastmcp import FastMCP
    except ImportError:
        print("The 'fastmcp' helper isn't installed yet. Run this once:")
        print("    python3 -m pip install fastmcp httpx python-dotenv")
        sys.exit(1)

    cid = os.environ.get("WORKIVA_CLIENT_ID")
    csec = os.environ.get("WORKIVA_CLIENT_SECRET")
    if not (cid and csec):
        print("Missing WORKIVA_CLIENT_ID / WORKIVA_CLIENT_SECRET. These are set "
              "in your Claude Desktop settings (see the setup notes).")
        sys.exit(1)

    w = Workiva(
        cid,
        csec,
        os.environ.get("WORKIVA_IAM_BASE_URL", DEFAULT_IAM),
        os.environ.get("WORKIVA_PLATFORM_BASE_URL", DEFAULT_PLATFORM),
        API_VERSION,
        os.environ.get("WORKIVA_SCOPES", ""),
    )

    # Org and workspace IDs are needed for user-lookup endpoints, which are
    # scoped under /organizations/{orgId}/workspaces/{workspaceId}/. Set these
    # in your Claude Desktop settings alongside the client ID/secret. The
    # workspace ID is the 'w_...' value in a task/document sourceUrl; the org
    # ID comes from the Workiva Organization Admin UI.
    DEFAULT_ORG_ID = os.environ.get("WORKIVA_ORG_ID", "")
    DEFAULT_WORKSPACE_ID = os.environ.get("WORKIVA_WORKSPACE_ID", "")

    mcp = FastMCP(
        name="workiva",
        instructions=(
            "Interact with a Workiva workspace: read and edit documents, "
            "spreadsheets, and files; look up workspace members by name; "
            "create, update, delete, and manage task approvals. Write "
            "operations require the appropriate scopes on the service grant "
            "(file:write, task:write); user lookup needs organization:read."
        ),
    )

    # Set WORKIVA_READ_ONLY=true to skip registering every tool that creates,
    # edits, deletes, or publishes content.
    READ_ONLY = os.environ.get("WORKIVA_READ_ONLY", "").lower() in ("1", "true", "yes")

    def write_tool(fn):
        return fn if READ_ONLY else mcp.tool(fn)

    @mcp.tool
    async def list_documents(limit: int = 50) -> list:
        """List documents in the workspace (up to `limit`)."""
        return await w.list("/documents", max_items=limit)

    @mcp.tool
    async def get_document(document_id: str) -> dict:
        """Retrieve a single document's details by id."""
        return await w.request("GET", f"/documents/{document_id}")

    @mcp.tool
    async def list_spreadsheets(limit: int = 50) -> list:
        """List spreadsheets in the workspace (up to `limit`)."""
        return await w.list("/spreadsheets", max_items=limit)

    @mcp.tool
    async def get_spreadsheet(spreadsheet_id: str) -> dict:
        """Retrieve a single spreadsheet's details by id."""
        return await w.request("GET", f"/spreadsheets/{spreadsheet_id}")

    @mcp.tool
    async def list_sheets(spreadsheet_id: str) -> list:
        """List the sheets within a spreadsheet."""
        return await w.list(f"/spreadsheets/{spreadsheet_id}/sheets")

    @mcp.tool
    async def get_range_values(spreadsheet_id: str, sheet_id: str,
                               cell_range: str) -> dict:
        """Read values from a sheet range, e.g. cell_range='A1:D20'."""
        return await w.request(
            "GET",
            f"/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/values/{cell_range}",
            params={"$valuestyle": "calculated"},
        )
    @mcp.tool
    async def get_sheet_data(spreadsheet_id: str, sheet_id: str,
                             cell_range: str = "") -> dict:
        """Retrieve data (cell values and formatting) from a sheet.

        cell_range is optional and A1-style, e.g. 'A1:D20'. Leave it blank
        to return the whole sheet.
        """
        params = {}
        if cell_range:
            params["$cellrange"] = cell_range
        return await w.request(
            "GET",
            f"/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/sheetdata",
            params=params,
        )

    @mcp.tool
    async def search_sheet_cells(spreadsheet_id: str, sheet_id: str,
                                 search_term: str,
                                 cell_range: str = "A1:Z500",
                                 case_sensitive: bool = False) -> list:
        """Find cells in a sheet whose value contains search_term. Read-only.

        Use this INSTEAD of get_sheet_data when you just need to locate one
        line item (e.g. "Net Sales") in a large sheet - get_sheet_data pulls
        every cell's full formatting and can be too large to return.

        cell_range: A1-style scan window (default 'A1:Z500'). Widen this
        (e.g. 'A1:AZ1000') if your sheet is bigger than that.

        Each match includes BOTH addressing schemes so you never have to
        convert by hand:
          - a1: human-readable reference, e.g. 'G18'
          - row / column: 0-based indices, ready to pass straight into
            create_table_anchor, get_table_cells, or
            insert_table_destination_link.

        Returns an empty list if nothing matches - try widening cell_range
        or shortening search_term.
        """
        payload = await w.request(
            "GET",
            f"/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/values/{cell_range}",
            params={"$valuestyle": "calculated"},
        )
        rows_data = payload.get("data", [])
        values = rows_data[0].get("values", []) if rows_data else []

        start_ref = cell_range.split(":")[0]
        start_letters, start_row_num = _split_a1(start_ref)
        start_col_index = _col_letters_to_index(start_letters)
        start_row_index = start_row_num - 1

        term = search_term if case_sensitive else search_term.lower()
        matches = []
        for r, row in enumerate(values):
            for c, cell_value in enumerate(row):
                if cell_value in (None, ""):
                    continue
                haystack = str(cell_value)
                if not case_sensitive:
                    haystack = haystack.lower()
                if term in haystack:
                    abs_row = start_row_index + r
                    abs_col = start_col_index + c
                    matches.append({
                        "a1": f"{_col_index_to_letters(abs_col)}{abs_row + 1}",
                        "row": abs_row,
                        "column": abs_col,
                        "value": cell_value,
                    })
        return matches

    @mcp.tool
    async def list_files(limit: int = 50) -> list:
        """List files/folders in the workspace (up to `limit`)."""
        return await w.list("/files", max_items=limit)

    # ------------------------------------------------------------------ #
    # Users: resolve natural-language names to user IDs for tasking
    # ------------------------------------------------------------------ #
    @mcp.tool
    async def list_users(name_contains: str | None = None,
                         email_contains: str | None = None,
                         limit: int = 50,
                         organization_id: str | None = None,
                         workspace_id: str | None = None) -> list:
        """List members of a workspace (display name, email, user id).
        Read-only. Needs 'organization:read' scope.

        Use this to find the user IDs that create_task / update_task need
        for assignees and approval-step participants. Filter by name or
        email to narrow the results.

        name_contains: case-insensitive partial match on display name,
            e.g. 'David' or 'Taylor'.
        email_contains: partial match on email address.
        organization_id / workspace_id: default to the WORKIVA_ORG_ID and
            WORKIVA_WORKSPACE_ID environment variables; override only if
            you need to look in a different workspace.

        Returns a list of {id, displayName, email} dicts. 'id' is the
        value to pass into create_task's assignee_ids.
        """
        org = organization_id or DEFAULT_ORG_ID
        ws = workspace_id or DEFAULT_WORKSPACE_ID
        if not (org and ws):
            raise WorkivaError(
                400,
                "Missing organization/workspace ID. Set WORKIVA_ORG_ID and "
                "WORKIVA_WORKSPACE_ID in your Claude Desktop settings, or "
                "pass organization_id / workspace_id explicitly."
            )
        filters = []
        if name_contains:
            filters.append(f"user.displayName contains '{name_contains}'")
        if email_contains:
            filters.append(f"user.email contains '{email_contains}'")
        params = {}
        if filters:
            params["$filter"] = " and ".join(filters)
        memberships = await w.list(
            f"/organizations/{org}/workspaces/{ws}/memberships",
            max_items=limit,
            params=params,
        )
        # Flatten the membership envelope down to just the user fields.
        out = []
        for m in memberships:
            u = m.get("user", {}) if isinstance(m, dict) else {}
            out.append({
                "id": u.get("id"),
                "displayName": u.get("displayName"),
                "email": u.get("email"),
            })
        return out

    @mcp.tool
    async def find_user_id(name: str,
                           organization_id: str | None = None,
                           workspace_id: str | None = None) -> dict:
        """Resolve a person's name to their Workiva user ID for tasking.
        Read-only. Needs 'organization:read' scope.

        This is a convenience wrapper over list_users for the common case
        of 'assign this to <name>'. It searches display names for `name`.

        Returns one of:
          - {"id": "...", "displayName": "...", "email": "..."} on a
            single unambiguous match (ready to pass to create_task).
          - {"error": "no_match", ...} if nobody matched.
          - {"error": "ambiguous", "matches": [...]} if more than one
            person matched, so you can confirm which one is meant rather
            than silently assigning to the wrong person.

        For an exact match when several names overlap (e.g. two Davids),
        pass a fuller name like 'David Taylor'.
        """
        candidates = await list_users(
            name_contains=name,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        candidates = [c for c in candidates if c.get("id")]
        if not candidates:
            return {"error": "no_match",
                    "message": f"No workspace member matched '{name}'."}
        if len(candidates) > 1:
            return {"error": "ambiguous",
                    "message": f"{len(candidates)} members matched '{name}'. "
                               "Confirm which one, then pass their id "
                               "directly to create_task.",
                    "matches": candidates}
        return candidates[0]

    # ------------------------------------------------------------------ #
    # Tasks: list, get, create, update, delete, approval actions
    # ------------------------------------------------------------------ #
    @mcp.tool
    async def list_tasks(limit: int = 50,
                         filter: str | None = None,
                         order_by: str | None = None) -> list:
        """List tasks in the workspace (up to `limit`). Read-only.
        Needs 'task:read' scope.

        filter (optional): OData-style filter string. Combine clauses
        with 'and'. Available fields and operators:

          assignees              eq, in
          approvalSteps.participants  eq, in
          status                 eq
          title                  contains
          taskType               contains
          location.resource      eq
          location.segment       eq
          dueDate                eq, lt, gt, le, ge
          created.dateTime       eq, lt, gt, le, ge
          modified.dateTime      eq, lt, gt, le, ge

        Examples:
          "status eq 'Created'"
          "assignees eq 'V0ZVc2VyHzU1MDg...' and status eq 'Created'"
          "title contains 'quarterly'"
          "dueDate lt '2026-12-31T00:00:00Z'"

        order_by (optional): comma-separated sort expressions.
        Sortable fields (each supports asc/desc):
          title, status, taskType, dueDate,
          created.dateTime, modified.dateTime,
          approvalSteps.participants

        Example: "dueDate asc" or "status desc, created.dateTime asc"
        """
        params = {}
        if filter:
            params["$filter"] = filter
        if order_by:
            params["$orderBy"] = order_by
        return await w.list("/tasks", max_items=limit, params=params)

    @mcp.tool
    async def get_task(task_id: str) -> dict:
        """Retrieve a single task by id, including full approval step
        detail, assignees, status, location, and responses.
        Read-only. Needs 'task:read' scope.
        """
        return await w.request("GET", f"/tasks/{task_id}")

    @write_tool
    async def create_task(title: str,
                          description: str = "",
                          assignee_ids: list[str] | None = None,
                          due_date: str | None = None,
                          resource_id: str | None = None,
                          segment_id: str | None = None,
                          approval_steps: list[dict] | None = None,
                          notifications_disabled: bool = False) -> dict:
        """Create a new task. CHANGES REAL CONTENT. Needs 'task:write'.

        Parameters:
          title (required): The task title.
          description: Optional task description / instructions.
          assignee_ids: List of Workiva user IDs to assign. Each is
              the base-64-ish string you see in user profiles, e.g.
              'V0ZVc2VyHzU1MDg3OTc0MDE4MDg4OTY'.
          due_date: ISO 8601 datetime for the deadline, e.g.
              '2026-12-31T00:00:00Z'. Converted to UTC if tz included.
          resource_id: Attach the task to a Document, Spreadsheet,
              Presentation, or Sustainability resource by its id.
              Once set, the location CANNOT be changed.
          segment_id: Narrow the attachment to a specific Section
              (document), Sheet (spreadsheet), or Slide (presentation).
              Only meaningful when resource_id is also provided.
          approval_steps: Optional list of approval step objects. Each
              step is a dict with:
                completionMode: 'ONE' (any participant completes it)
                                or 'ALL' (every participant must respond)
                participants: list of {'id': '<user_id>', 'type': 'USER'}
                dueDate: optional ISO 8601 deadline for this step
              Example:
                [{"completionMode": "ONE",
                  "dueDate": "2026-07-15T00:00:00Z",
                  "participants": [
                    {"id": "V0ZVc2VyHzY0MTI5...", "type": "USER"}
                  ]}]
          notifications_disabled: True to suppress task-creation
              notifications (reminders still fire). Default False.

        Returns the created Task object including its new 'id',
        'status' (defaults to 'Created'), 'sourceUrl', and timestamps.
        """
        body: dict = {"title": title}
        if description:
            body["description"] = description
        if assignee_ids:
            body["assignees"] = [{"id": uid, "type": "USER"}
                                 for uid in assignee_ids]
        if due_date:
            body["dueDate"] = due_date
        if resource_id:
            loc: dict = {"resource": resource_id}
            if segment_id:
                loc["segment"] = segment_id
            body["location"] = loc
        if approval_steps:
            body["approvalSteps"] = approval_steps
        if notifications_disabled:
            body["notificationsDisabled"] = True
        return await w.request("POST", "/tasks", json=body)

    @write_tool
    async def update_task(task_id: str, patches: list[dict]) -> dict:
        """Partially update a task using JSON Patch. CHANGES REAL CONTENT.
        Needs 'task:write'.

        IMPORTANT side effects:
          - Changing 'assignees' automatically RESTARTS the task.
          - Changing 'approvalSteps' restarts the task if any
            participants who already approved are modified.
          - Changing 'location' restarts the task.

        patches: A list of JSON Patch operations. Each is a dict with:
          op:    'replace' (the only supported operation)
          path:  one of /title, /description, /assignees, /dueDate,
                 /approvalSteps, /location, /owner
          value: the new value for that field

        Examples:
          Change the title:
            [{"op": "replace", "path": "/title",
              "value": "Updated title"}]

          Change the due date:
            [{"op": "replace", "path": "/dueDate",
              "value": "2026-09-30T00:00:00Z"}]

          Reassign the task (WARNING: restarts approval):
            [{"op": "replace", "path": "/assignees",
              "value": [{"id": "V0ZVc2VyHz...", "type": "USER"}]}]

          Multiple changes in one call:
            [{"op": "replace", "path": "/title", "value": "New title"},
             {"op": "replace", "path": "/description",
              "value": "Updated instructions"}]

        Returns the updated Task object.
        """
        return await w.request("PATCH", f"/tasks/{task_id}", json=patches)

    @write_tool
    async def delete_task(task_id: str) -> str:
        """Delete a task by id. DESTRUCTIVE — cannot be undone.
        Needs 'task:write'.

        Returns a confirmation message (the API returns 204 No Content).
        """
        await w.request("DELETE", f"/tasks/{task_id}")
        return f"Task {task_id} deleted successfully."

    @write_tool
    async def submit_task_action(task_id: str, action: str,
                                  comment: str | None = None) -> dict:
        """Submit an action on a task's approval step. CHANGES REAL
        CONTENT. Needs 'task:write'.

        For tasks with multiple approval steps, this is the ONLY way
        to advance the task through its approval process. The task's
        status field updates automatically as steps are completed.

        action (required): One of:
          SUBMIT   — submit the task for approval
          APPROVE  — approve the current approval step
          REJECT   — reject the current approval step
          CANCEL   — cancel the submission
          RESTART  — restart the approval process
          SKIP     — skip the current approval step

        comment (optional): A note to attach to the action, e.g.
          "Looks good, approved" or "Please fix the revenue line".

        Returns the updated Task object with the new status and
        the action recorded in approvalSteps[].responses[].
        """
        body: dict = {"action": action}
        if comment:
            body["comment"] = comment
        return await w.request(
            "POST", f"/tasks/{task_id}/actionSubmission", json=body
        )
# ------------------------------------------------------------------ #
    # Content editing: sections → rich text → edits
    # ------------------------------------------------------------------ #
    @mcp.tool
    async def list_sections(document_id: str) -> list:
        """List the sections of a document. Each section holds body text,
        headers, and footers as separate rich text containers."""
        return await w.list(f"/documents/{document_id}/sections")

    @mcp.tool
    async def get_section(document_id: str, section_id: str) -> dict:
        """Get a single section's details, including its richTextId values.

        The response includes body.richText (the main text), plus
        headers.odd.richText and footers.odd.richText for header/footer.
        You need the richTextId to read or edit the section's content.
        """
        return await w.request(
            "GET", f"/documents/{document_id}/sections/{section_id}"
        )

    @mcp.tool
    async def get_rich_text_paragraphs(rich_text_id: str) -> dict:
        """Read the paragraphs (actual text content) of a rich text container.

        Use the richTextId from get_section (e.g. the body.richText value).
        Returns paragraphs with their text spans, formatting, and element
        details. The response includes a 'revision' string you'll need if
        you want to edit the content afterward.
        """
        return await w.request(
            "GET", f"/content/richText/{rich_text_id}/paragraphs"
        )

    @write_tool
    async def edit_rich_text(rich_text_id: str, revision: str,
                             edits: list, isolate_edits: bool = True) -> dict:
        """Edit the text content of a rich text container. CHANGES REAL CONTENT.

        Requires 'file:write' scope on the grant.

        Parameters:
          rich_text_id: from get_section's body.richText (or header/footer)
          revision: from get_rich_text_paragraphs response (prevents conflicts)
          isolate_edits: True = offsets are relative to the original revision
                         (easier). False = each edit sees the effect of prior
                         edits in the same batch.
          edits: list of edit operations. Common types:

            Insert text:
              {"type": "insertText", "insertText": {
                  "insertAt": {"paragraphIndex": 0, "offset": 0},
                  "text": "Hello world\\n"}}

            Delete text:
              {"type": "deleteText", "deleteText": {
                  "selection": {
                      "start": {"paragraphIndex": 0, "offset": 5},
                      "stop":  {"paragraphIndex": 0, "offset": 11}}}}

            Format text (bold, italic, etc.):
              {"type": "formatText", "formatText": {
                  "selection": {
                      "start": {"paragraphIndex": 0, "offset": 0},
                      "stop":  {"paragraphIndex": 0, "offset": 5}},
                  "bold": true}}

        This is async — returns an operationLocation. Extract the operation
        id and pass it to get_operation to check completion, then to
        get_edit_results to see what was created (e.g. new table ids).
        """
        body = {
            "revision": revision,
            "isolateEdits": isolate_edits,
            "data": edits,
        }
        return await w.request(
            "POST", f"/content/richText/{rich_text_id}/edit", json=body
        )

    # ------------------------------------------------------------------ #
    # Operations (needed for any write/edit)
    # ------------------------------------------------------------------ #
    @mcp.tool
    async def get_operation(operation_id: str) -> dict:
        """Check the status of a background operation by its id.

        Write actions return an operationLocation ending in
        /operations/<id>. Poll this until it reports completion.
        """
        return await w.request("GET", f"/operations/{operation_id}")

    @mcp.tool
    async def get_edit_results(operation_id: str) -> dict:
        """Get the results of a completed rich text edit operation.

        After get_operation shows the edit is done, call this to retrieve
        the new revision and any created objects (e.g. table ids).
        """
        return await w.request(
            "GET",
            f"/operations/{operation_id}/richTextBatchEditResults",
        )
    # ------------------------------------------------------------------ #
    # Tables: read cells / edit cells
    # ------------------------------------------------------------------ #
    @mcp.tool
    async def get_table_cells(table_id: str, start_row: int | None = None,
                              stop_row: int | None = None,
                              start_column: int | None = None,
                              stop_column: int | None = None) -> dict:
        """Read the cells of a table. Returns each cell's value plus a
        'revision' string you'll need in order to edit it.

        Row/column bounds are optional, inclusive, and zero-based; omit them
        all to read the whole table.
        """
        params = {}
        if start_row is not None:
            params["startRow"] = start_row
        if stop_row is not None:
            params["stopRow"] = stop_row
        if start_column is not None:
            params["startColumn"] = start_column
        if stop_column is not None:
            params["stopColumn"] = stop_column
        return await w.request(
            "GET", f"/content/tables/{table_id}/cells", params=params
        )

    @write_tool
    async def edit_table_cells(table_id: str, edits: list,
                               revision: str | None = None,
                               locked_cell_edit_mode: str = "strict") -> dict:
        """Edit cells in a table. CHANGES REAL CONTENT. Needs 'file:write'.

        edits: list of operations per Workiva's TableCellsBatchEdit schema.
        Confirmed example (clear formatting on a range of cells):
          [{"type": "clearCellsFormat", "clearCellsFormat": {
              "clearAll": true,
              "ranges": [{"startRow": 0, "startColumn": 0,
                          "stopRow": 2, "stopColumn": 2}]}}]

        NOTE: Setting a cell's value is done via a RichCellBatchEdit entry in
        `edits` (same shape as edit_rich_text's edits, e.g. insertText,
        addressed at the cell). If the target cell currently holds a
        CellFormulaValue or CellPlainTextValue, clear it first — a rich
        text edit isn't accepted until the existing value is cleared.

        revision: from get_table_cells (pins a stable cell location). Omit to
        use the latest revision.

        Async - returns an operationLocation ending in /operations/<id>; pass
        that <id> to get_operation to confirm completion.
        """
        body = {"data": edits, "lockedCellEditMode": locked_cell_edit_mode}
        if revision:
            body["revision"] = revision
        return await w.request(
            "POST", f"/content/tables/{table_id}/cells/edit", json=body
        )
    @write_tool
    async def create_table(rich_text_id: str, revision: str,
                           row_count: int, column_count: int,
                           paragraph_index: int = 0, offset: int = 0,
                           isolate_edits: bool = True) -> dict:
        """Insert a new empty table into a section's rich text. CHANGES REAL
        CONTENT. Needs 'file:write'.

        Tables live inside a section's rich text, so 'creating' one means
        inserting it into a rich text container. Get rich_text_id and revision
        the same way as edit_rich_text: get_section -> body.richText, then
        get_rich_text_paragraphs for the current revision.

        row_count / column_count: size of the new table.
        paragraph_index / offset: where to place it (default: the very start).

        Async - returns an operationLocation ending in /operations/<id>. After
        get_operation shows it completed, call get_edit_results with that <id>
        to get the NEW TABLE ID, which you then use with get_table_cells /
        edit_table_cells.
        """
        body = {
            "revision": revision,
            "isolateEdits": isolate_edits,
            "data": [
                {
                    "type": "insertTable",
                    "insertTable": {
                        "insertAt": {"paragraphIndex": paragraph_index,
                                     "offset": offset},
                        "rowCount": row_count,
                        "columnCount": column_count,
                    },
                }
            ],
        }
        return await w.request(
            "POST", f"/content/richText/{rich_text_id}/edit", json=body
        )
    # ------------------------------------------------------------------ #
    # Linking: source anchor -> destination link -> publish
    # ------------------------------------------------------------------ #
    @write_tool
    async def create_table_anchor(table_id: str, row: int, column: int,
                                  revision: str) -> dict:
        """Create a SOURCE-LINK anchor on a single table/sheet cell. CHANGES
        CONTENT. Needs 'file:write'.

        A source link must live on an anchor; this creates that anchor on one
        cell (table source links are single-cell only). For a spreadsheet, get
        table_id from list_sheets -> sheet.table, and revision from
        get_table_cells.

        Async - returns an operationLocation ending in /operations/<id>. Pass
        that <id> to get_table_anchor_results to get the new anchor id.
        """
        body = {
            "range": {"startRow": row, "stopRow": row,
                      "startColumn": column, "stopColumn": column},
            "revision": revision,
        }
        return await w.request(
            "POST", f"/content/tables/{table_id}/anchors/creation", json=body
        )

    @mcp.tool
    async def get_table_anchor_results(operation_id: str) -> dict:
        """Get results of a completed create_table_anchor operation - returns
        the new anchor id (data[].anchor) and the new revision."""
        return await w.request(
            "GET", f"/operations/{operation_id}/tableAnchorCreationResults"
        )

    @write_tool
    async def insert_rich_text_destination_link(rich_text_id: str,
                                                revision: str,
                                                source_anchor: str,
                                                paragraph_index: int = 0,
                                                offset: int = 0) -> dict:
        """Insert a DESTINATION link inline in a paragraph, pointing at a
        source anchor. CHANGES CONTENT. Needs 'file:write'.

        rich_text_id + revision: from get_section -> body.richText, then
        get_rich_text_paragraphs. source_anchor: from get_table_anchor_results.

        Async - Delete the original hard coded text since this does not replace text.Check with get_operation, then publish the SOURCE side so the
        value flows to this destination. Only need to publish if operation changes the Source Side value and not purely just creating a destination link.
        """
        body = {
            "revision": revision,
            "data": [
                {
                    "type": "insertDestinationLink",
                    "insertDestinationLink": {
                        "insertAt": {"paragraphIndex": paragraph_index,
                                     "offset": offset},
                        "sourceAnchor": source_anchor,
                    },
                }
            ],
        }
        return await w.request(
            "POST", f"/content/richText/{rich_text_id}/links/edit", json=body
        )

    @write_tool
    async def insert_table_destination_link(table_id: str, revision: str,
                                            source_anchor: str,
                                            row: int = 0,
                                            column: int = 0) -> dict:
        """Insert a DESTINATION link into a table cell, pointing at a source
        anchor. CHANGES CONTENT. Needs 'file:write'.

        table_id + revision: the destination table (e.g. a table in the 10-Q),
        from get_table_cells. source_anchor: from get_table_anchor_results.

        Async - check with get_operation, then publish the source side.Only need to publish if operation chance the Source Side value and not purly just creating a destination link.
       
        """
        body = {
            "revision": revision,
            "data": [
                {
                    "type": "insertCellDestinationLink",
                    "insertCellDestinationLink": {
                        "insertAt": {"row": row, "column": column},
                        "sourceAnchor": source_anchor,
                    },
                }
            ],
        }
        return await w.request(
            "POST", f"/content/tables/{table_id}/links/edit", json=body
        )

    @write_tool
    async def publish_spreadsheet_links(spreadsheet_id: str,
                                        publish_type: str = "ownLinks") -> dict:
        """Publish a spreadsheet's source links so their values flow to
        destinations. CHANGES CONTENT. Needs 'file:write'.

        publish_type: 'ownLinks' (default, confirmed) publishes only links the
        integration user created. Publishing ALL links needs document-owner
        permission (value name not confirmed here).

        You only need to publish links 

        Async - check with get_operation.
        """
        return await w.request(
            "POST", f"/spreadsheets/{spreadsheet_id}/links/publication",
            json={"publishType": publish_type},
        )
    @mcp.tool
    async def list_table_anchors(table_id: str, limit: int = 50) -> list:
        """List the anchors on a table/sheet (up to `limit`). Read-only.

        Each anchor includes its 'id' (reusable as a source_anchor for a new
        destination link), its cell 'range', and 'extensionTypes' - if that
        contains 'destinationLink', the anchor already feeds one or more links.

        For a spreadsheet, get table_id from list_sheets -> sheet.table.
        """
        return await w.list(f"/content/tables/{table_id}/anchors",
                            max_items=limit)

    @mcp.tool
    async def get_destination_link(destination_link_id: str) -> dict:
        """Retrieve a destination link by id. Read-only.

        Returns the link's details, including source.anchor.anchor - the id of
        the SOURCE anchor it points at, which you can reuse as the
        source_anchor for a new destination link. Also shows 'status'
        (e.g. 'connected'). You can find a destination link's id when reading
        a document's rich text (get_rich_text_paragraphs) or table cells.
        """
        return await w.request(
            "GET", f"/content/destinationLinks/{destination_link_id}"
        )
    try:
        mcp.run()
    finally:
        asyncio.run(w.aclose())


if __name__ == "__main__":
    if "--check" in sys.argv:
        run_check()
    else:
        run_server()
