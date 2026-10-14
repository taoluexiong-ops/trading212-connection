#!/usr/bin/env python3
"""从源码构建并临时签名应用 / Build and ad hoc sign the app from source."""
from pathlib import Path
import json
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise SystemExit('需要 Apple Silicon macOS。 / Requires Apple Silicon macOS.')
    if not shutil.which('swiftc'):
        raise SystemExit('请先安装 Apple 命令行开发工具。 / Install Apple command line developer tools first.')
    output = root / '.build'
    output.mkdir(exist_ok=True)
    template = root / 'app/Trading212 Connection.app'
    destination = output / template.name
    archive = output / 'Trading212 Connection-macOS-arm64.zip'
    with tempfile.TemporaryDirectory(prefix='trading212-build-', dir='/private/tmp') as temporary:
        staged = Path(temporary) / template.name
        shutil.copytree(template, staged, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', '_CodeSignature'))
        executable = staged / 'Contents/MacOS/Trading212Connection'
        executable.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['swiftc', '-parse-as-library', '-O', '-target', 'arm64-apple-macosx13.0',
                        '-module-cache-path', str(output / 'swift-cache'),
                        str(root / 'Localization.swift'), str(root / 'Manager.swift'),
                        '-o', str(executable), '-framework', 'SwiftUI', '-framework', 'AppKit'], check=True)
        executable.chmod(0o755)
        subprocess.run(['/usr/bin/xattr', '-cr', str(staged)], check=True)
        subprocess.run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', str(staged)], check=True)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(staged)], check=True)
        if destination.exists():
            info = plistlib.loads((destination / 'Contents/Info.plist').read_bytes())
            if info.get('CFBundleIdentifier') != 'local.codex.trading212.connection':
                raise SystemExit('已有构建不属于本项目，未覆盖。 / Existing build is not owned by this project; not overwritten.')
            shutil.rmtree(destination)
        shutil.copytree(staged, destination, copy_function=shutil.copy)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
            for file in sorted(staged.rglob('*')):
                if file.is_file(): package.write(file, file.relative_to(staged.parent))
    print('构建完成 / Build complete')
    print(json.dumps({'app': str(destination), 'archive': str(archive)}, ensure_ascii=False))


if __name__ == '__main__': main()
