#!/usr/bin/env python3
"""Open the bundled native manager without handling credentials."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[3]
stable=Path.home()/'Applications/Trading212 Connection.app'
app = stable if stable.is_dir() else root / 'app/Trading212 Connection.app'
if not app.is_dir():
    raise SystemExit('Trading212 Connection应用文件缺失，请先构建并安装。 / App missing; build and install it first.')
subprocess.run(['/usr/bin/open', str(app)], check=True)
