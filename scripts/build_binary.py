"""Build a standalone executable on the target OS and architecture."""
import hashlib
import os
from pathlib import Path
import platform
import shutil
import ssl
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    build = root / "build"
    build.mkdir(exist_ok=True)
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(build / "pyinstaller-cache"))
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--console", "--name", "ask-ai",
        "--distpath", str(root / "dist"), "--workpath", str(build / "pyinstaller"),
        "--specpath", str(build), "--paths", str(root),
        "--runtime-hook", str(root / "scripts/frozen_tls.py"),
    ]
    # Include a CA snapshot for interpreters whose SSL paths point at the build host.
    certificate = ssl.get_default_verify_paths().cafile
    if certificate:
        bundle = build / "cacert.pem"
        shutil.copyfile(certificate, bundle)
        args.extend(["--add-data", f"{bundle}:certs"])
    args.append(str(root / "ask-ai"))
    subprocess.run(args, cwd=root, env=env, check=True)
    binary = root / "dist" / ("ask-ai.exe" if os.name == "nt" else "ask-ai")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (root / "dist/SHA256SUMS").write_text(f"{digest}  {binary.name}\n", encoding="utf-8")
    info = f"Platform: {platform.system()} {platform.machine()}\nBuild libc: {' '.join(platform.libc_ver())}\nPython bundled: {platform.python_version()}\n"
    (root / "dist/BUILD-INFO.txt").write_text(info, encoding="utf-8")
    print(f"\nBuilt {binary} ({binary.stat().st_size / 1024 / 1024:.1f} MiB)\n{info}")


if __name__ == "__main__":
    main()
