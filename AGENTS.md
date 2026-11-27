# Project development rules

- The only permanent working directory is `C:\kaspibot`. Do not create additional project copies unless necessary.
- Repository: `Ybuuc/kaspi-store-manager`; working branch: `integration/archive-first-course`.
- Implement and test locally first. After successful checks, commit and push to the working branch; verify local and remote source revisions match.
- Never merge into `main` without separate permission. Never force push or use `git reset --hard`.
- Keep .env, tokens, passwords, private URLs, TLS private keys, production databases, customer data, real seller XML, backups, logs and virtual environments local.
- Preserve shop data. Use SQLite online backup before changes to a working database. Never derive physical inventory, purchase costs or movements from declared XML availability.
- API and XML integrations are read-only and independent. TLS verification is mandatory. Local catalogue access requires explicit opt-in and is restricted to localhost, 127.0.0.1 and ::1 on port 8443.
- Use free solutions and respect limited resources: Intel Core i3-1115G4, 8 GB RAM, Intel UHD Graphics. Do not enable Windows startup services without permission.
