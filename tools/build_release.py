"""Build a portable source distribution without database, credentials or private shop data."""
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
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
    allowed = {'.py','.ps1','.html','.css','.js','.json','.md','.toml','.txt','.yml','.yaml','.sql','.csv','.ipynb','.png','.excalidraw'}
    files = sorted({root / name for name in names if name and (root / name).is_file()
                    and (Path(name).suffix in allowed or Path(name).name in {'Dockerfile','.dockerignore','.gitignore','.env.example','requirements.lock'})
                    and not any(part in {'dist','instance','backups','.venv','__pycache__'} for part in Path(name).parts[:-1])})
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
