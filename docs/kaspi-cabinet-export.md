# Fresh XML from the Kaspi seller cabinet

This optional source is read-only: it uses the existing cabinet command
**Управление товарами → Действия с файлами → Скачать прайс-лист в XML**.
It does not use the order API token and never uploads a price list or edits a
product. The export interface was observed in the real cabinet; it is not a
documented public Token API. Kaspi may change it or expire the login session.

The cabinet exposes authenticated export types/status/trigger operations under
`https://mc.shop.kaspi.kz/offers/api/v1/offer/export/`. They reject unauthenticated
reads with HTTP 401. No anonymous, permanently refreshed XML URL was configured
in this shop. The normal cabinet automatic-upload setting pulls data INTO Kaspi;
it is not an export to the application, and this implementation does not change it.

## Local setup

Use the existing Google Chrome installation (Playwright `channel=chrome`), without
installing a second Chromium distribution. One browser is started per scheduled
export and closed afterwards. Login uses a separate, visible, isolated context.
Your existing Chrome profile and the in-app browser's credentials are not copied.

```dotenv
KASPI_CATALOG_EXPORT=1
KASPI_CATALOG_MERCHANT_ID=YOUR_NUMERIC_MERCHANT_ID
KASPI_ALLOW_LOCAL_CATALOG=1
KASPI_CATALOG_URL=https://localhost:8443/catalog.xml
KASPI_CATALOG_CA_BUNDLE=.local-catalog/ca.pem
KASPI_AUTO_SYNC=1
KASPI_SYNC_INTERVAL_SECONDS=300
```

From `C:\kaspibot`, with the local HTTPS server running:

```powershell
.\.venv\Scripts\python.exe tools\kaspi_catalog_login.py
```

Enter the password/SMS manually in the separate window. The helper stores only
the dedicated browser's session state in `.local-catalog/kaspi-session.json`,
protected by the folder's Windows ACL (current user and SYSTEM). Treat this file
like a password: keep it out of Git, Docker, screenshots and external uploads.
The helper validates the first real XML, publishes it and checks a real local
HTTPS read before importing. It reports readiness only after this succeeds.
Restart the web application after changing `.env`. Renew a rejected/expired
session by running the same helper; password and SMS automation is not used.

## Scheduled refresh and preservation

The existing SQLite lease covers export, publication and import in the same
300-second cycle, so no second scheduler or Windows startup task is added.
Before publication, the full XML is validated and its merchant/date checked
against the last good database snapshot. A temporary file, fsync and same-folder
atomic replace prevent a partially written public catalogue. The exact bytes
served over local TLS must match the new export. If download/import fails, the
previous file is restored, unless an operator has already replaced it.
The API worker is queued independently even when source login/export fails.
Status distinguishes cabinet export, HTTPS access and XML import; a retained
old catalogue is not reported as a newly obtained store state.

The source uses only its own browser session. Scheduled mode uses an exact allowlist for observed read-only POST operations
(the getMerchant/getOrderCounters GraphQL queries, offer lists/filters and an
empty-body configuration read), plus the export-job trigger. GraphQL mutations,
configuration values and offer edits/uploads remain blocked. The export job
does not modify seller data. Refreshed session state is saved locally after
a successful export. TLS/hostname checking stays on. Signed file links are never
logged, are followed without redirects, and are limited to the observed Kaspi
hosts. Unexpected responses/HTML, another merchant, old XML, large files and
timeouts preserve the previous successful catalogue.

A check every five minutes is not a guarantee that Kaspi regenerates the export
every five minutes. The source date and unchanged-file result remain visible;
the application never rewrites the XML date to pretend the contents are newer.
Original card identity, physical stock, costs, reserves and movements remain
separate from declared XML availability. Removed-from-sale products absent from
the ACTIVE export are retained in the database.
