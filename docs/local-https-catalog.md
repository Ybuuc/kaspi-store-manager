# Local HTTPS seller catalogue on Windows

Run all commands in `C:\kaspibot`. Use the existing `.venv`; no paid service is needed.
The Python standard-library server binds only `127.0.0.1` and `::1` on port 8443,
serves only `/catalog.xml`, and never exposes a directory or another project file.
No Windows startup task is installed.

## Prepare once

```powershell
.\.venv\Scripts\python.exe tools\setup_local_catalog.py --export-saved-catalog
```

`--export-saved-catalog` recovers the latest genuine XML already stored in the
working database and refuses to replace an existing file. Omit this option when
supplying your own real `.local-catalog/catalog.xml`. It never generates products.
Keep the entire `.local-catalog` directory private. Restrict its Windows ACL to
your user and SYSTEM, particularly the two private PEM keys.

The generated certificate has SAN entries for localhost, 127.0.0.1 and ::1 and
expires after 365 days. The private CA is trusted explicitly by this application's
local catalogue client through the PEM bundle. It is not installed as a global
Windows/Python trusted root. Browsers may therefore show an untrusted issuer;
use the application or Python Requests with the CA bundle to verify this service.
Renew the certificate before expiry; keep the old material backed up privately.

Set locally in `.env` (never commit real configuration):

```dotenv
KASPI_ALLOW_LOCAL_CATALOG=1
KASPI_CATALOG_URL=https://localhost:8443/catalog.xml
KASPI_CATALOG_CA_BUNDLE=.local-catalog/ca.pem
KASPI_AUTO_SYNC=1
KASPI_SYNC_INTERVAL_SECONDS=300
```

The CA path is relative to the project root or absolute. Local access is disabled
by default and accepts exactly these three hostnames on port 8443. Private LAN,
link-local, other loopback spellings, HTTP, userinfo and redirects are rejected.
External feeds still require a global HTTPS destination on port 443 and public
CA trust. DNS addresses are validated and pinned for the request while retaining
the original certificate hostname and SNI. The XML client uses no API token,
proxy environment, `.netrc` credentials, ambient cookies or TLS environment override.
TLS verification, hostname checks, 16 MiB bounds and timeouts stay enabled.

## Run and stop

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\local-catalog.ps1 start
.\.venv\Scripts\python.exe app.py
# In another terminal:
powershell -NoProfile -ExecutionPolicy Bypass -File tools\local-catalog.ps1 status
powershell -NoProfile -ExecutionPolicy Bypass -File tools\local-catalog.ps1 stop
```

The server start script refuses an occupied port. Stop verifies the recorded PID's
executable and script before terminating it. The application stops with Ctrl+C.
The server is independent; stopping it does not stop API order synchronization.

Windows curl/Schannel error `CRYPT_E_NO_REVOCATION_CHECK` concerns that TLS
backend's revocation checking. This implementation uses Requests/OpenSSL and an
explicit private CA, without changing Windows revocation policy or disabling
certificate verification. Do not use insecure curl flags or `verify=False`.

## What automatic refresh means

The app checks immediately at startup and then every 300 seconds, in background.
The shared SQLite lease prevents overlapping scheduled ticks across web workers;
imports also take a SQLite write lock before freshness/duplicate checks, including
manual uploads. Errors preserve the last good snapshot and retry next cycle.
HTTPS availability, XML result, file date, offer count, import/check times, next
attempt, unchanged-file state and independent API state appear in the admin UI.
HTTP availability is an observation from the last scheduled attempt, not a live
monitor between checks. Manual XML upload continues to work.

Serving a static XML does not refresh its contents. An independent, authorized
producer must replace `.local-catalog/catalog.xml` with a current seller export
(prefer atomic file replacement). The recovered snapshot remains dated
19 November 2026; there is no automatic producer configured by these tools.
Prices remain dated XML observations, separate from local card prices, archived
prices and physical stock. Model/brand and declared availability belong to the
immutable snapshot; original card identity and inventory are retained.
