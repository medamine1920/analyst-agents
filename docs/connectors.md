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

### Optional, recommended: Google's file picker (lets you publish the app for everyone)

Without it, the console asks to read every spreadsheet in a user's Drive (`drive.readonly`). Google
calls that restricted access: the app can stay in testing (only listed test users, reconnecting every
7 days) or go through a paid security assessment before it is published. With the picker, each user
chooses the files the console may read, and the console asks only for `drive.file` (the files they
pick and the Sheets it creates), which needs no assessment.

1. **APIs & Services > Library**: enable the **Google Picker API**.
2. **APIs & Services > Credentials > Create credentials > API key**. Restrict it: application
   restriction **Websites**, `https://analyst.medamine.work/*`; API restriction **Google Picker API**.
3. Your **project number**: the project picker, or **IAM & Admin > Settings**.
4. Add to `~/apps/analyst-agents/.env`, then `docker compose up -d --force-recreate console`:

   ```
   GOOGLE_API_KEY=AIza...
   GOOGLE_APP_ID=123456789012
   ```

5. On the consent screen (**Data access**), remove the `drive.readonly` scope and keep `drive.file`.
   Then **Audience > Publish app**: anyone with a Google account can connect, with no test-user list.
6. Users already connected: **Disconnect**, then **Sign in with Google** again, so their access is
   the narrower one.

Each user connects their own Google account; the console stores each user's access encrypted and
private to them, and **Disconnect** also withdraws it at Google.

Without the picker the console asks for `drive.readonly` (to list and read files) and `drive.file` (to
create the Sheets it exports, and only those). The refresh token is stored encrypted; removing the connection deletes
it, and the user can also revoke access in their Google account.
