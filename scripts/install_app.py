#!/usr/bin/env python3
"""安装已构建的应用，保留连接数据 / Install the build and preserve connection data."""
from pathlib import Path
import os
import plistlib
import shutil
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / '.build/Trading212 Connection.app'
    destination = Path.home() / 'Applications/Trading212 Connection.app'
    if not source.is_dir():
        raise SystemExit('请先运行 scripts/build_app.py。 / Run scripts/build_app.py first.')
    destination.parent.mkdir(exist_ok=True)
    if destination.exists():
        info = plistlib.loads((destination / 'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIdentifier') != 'local.codex.trading212.connection':
            raise SystemExit('同名应用不属于本项目，未覆盖。 / Existing app is not owned by this project; not overwritten.')
    with tempfile.TemporaryDirectory(prefix='trading212-install-', dir=str(destination.parent)) as temporary:
        staged = Path(temporary) / source.name
        backup = Path(temporary) / 'previous.app'
        shutil.copytree(source, staged, copy_function=shutil.copy)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(staged)], check=True)
        if destination.exists(): os.replace(destination, backup)
        try: os.replace(staged, destination)
        except Exception:
            if backup.exists(): os.replace(backup, destination)
            raise
    print('安装完成，未改变账户或客户端权限。 / Installed; account and client permissions unchanged.')
    print(str(destination))


if __name__ == '__main__': main()
