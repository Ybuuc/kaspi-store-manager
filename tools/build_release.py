"""Build a portable source distribution without database, credentials or dev files."""
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path


def build():
    root = Path(__file__).resolve().parents[1]
    output = root / "dist"
    output.mkdir(exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    files = [root / name for name in ("app.py", "requirements.txt", "requirements.lock", "README.md")]
    files += sorted((root / "templates").glob("*.html"))
    files += sorted((root / "static").glob("*.css"))
    files.append(root / "demo" / "lz11_seed.json")
    manifest = {"commit": sha, "files": {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }}
    archive = output / f"kaspi-store-manager-{sha[:12]}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for p in files:
            bundle.write(p, p.relative_to(root).as_posix())
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(archive)


if __name__ == "__main__":
    build()
