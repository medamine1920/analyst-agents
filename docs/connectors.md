# Connectors: setting them up

Users connect tools on the **Connections** page. Credentials are encrypted on the server (key from
`CONSOLE_SECRET`, or a key file created in the volume) and never shown again after saving; only a
masked hint is listed. Every call goes through `app/connectors/http.py`: public addresses only
(checked again on every redirect), 30-second timeouts, retries with growing waits on busy services,
and a 50 MB limit.

| Connector | Brings data in | Receives results | What the user needs |
|---|---|---|---|
| File from a link | CSV, Excel, JSON; Google Sheets shared as "anyone with the link" | | The address |
| MCP server | Any tool that returns records | Any tool with a text argument | The endpoint address, and a token if it needs one |
| Google Sheets & Drive | Sheets, Excel and CSV files in their Drive | A new Sheet, one tab per table | To sign in with Google (the operator sets it up once, below) |
| Slack | | The answer and its first rows | An incoming webhook address |
| Microsoft Teams | | The answer and its first rows | A workflow webhook address |
| Airtable | The tables of a base | | A personal access token and the base id |
| Notion | A database shared with the integration | | An internal integration secret |
| HubSpot | Contacts, companies, deals, tickets | | A private app access token |


Imported data becomes a **data source** (one table per sheet, table or file), private to the user who
imported it, with generated metrics like any upload. Answers can be exported from the Export menu
under each answer: an Excel workbook of every table, or straight to a connected tool.

## Google Sheets & Drive: one-time setup by the operator

1. In Google Cloud Console, create a project, then **APIs & Services > Library**: enable the
   **Google Drive API** and the **Google Sheets API**.
2. **APIs & Services > OAuth consent screen**: external, app name "Analyst Console", your email;
   add the scopes `drive.readonly` and `drive.file`. While the app is in testing, add the Google
   accounts that may connect as test users.
3. **APIs & Services > Credentials > Create credentials > OAuth client ID**: type "Web application";
   authorized redirect URI: `https://analyst.medamine.work/api/connectors/google/callback`.
4. On the server, add to `~/apps/analyst-agents/.env`:

   ```
   GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=...
   CONSOLE_URL=https://analyst.medamine.work
   ```

   then `docker compose up -d --force-recreate console` in that folder. The Google card on the Connections
   page becomes active; until then it lists what is still missing and the exact return address to register.

The console asks for `drive.readonly` (to list and read files) and `drive.file` (to create the Sheets
it exports, and only those). The refresh token is stored encrypted; removing the connection deletes
it, and the user can also revoke access in their Google account.
