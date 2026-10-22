"""Install the local station user service and desktop startup (no root required).

Run with --demo for the existing fictional database. Configuration is also enabled for
future desktop logins even if the current tool cannot talk to the user systemd bus.
"""
import argparse
from pathlib import Path
import shutil
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--demo', action='store_true')
args = parser.parse_args()
home = Path.home()
station = Path(__file__).resolve().parent
repo = station.parent.parent
if not args.demo and not (station / 'local.env').exists():
    parser.error('Configure local.env for the official database, or explicitly use --demo.')
unit_dir = home / '.config/systemd/user'
unit_dir.mkdir(parents=True, exist_ok=True)
unit = unit_dir / 'adgvs-local.service'
mode = ' --demo' if args.demo else ''
# systemd quoting is not shell quoting; escape paths before writing unit values.
def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

# WorkingDirectory is a path directive, not an ExecStart argument list.
# Outer quotes become part of its path; use unit-file escapes instead.
def directory_value(value):
    return ''.join(
        f'\\x{ord(char):02x}' if char in '\\ \t\r\n\"\'' else char
        for char in str(value)
    ).replace('%', '%%')

unit.write_text(f'''[Unit]
Description=ADGVS local camera and paper validation app
StartLimitIntervalSec=120
StartLimitBurst=10

[Service]
Type=simple
WorkingDirectory={directory_value(repo)}
ExecStart=/bin/bash {quote(station / 'run-local.sh')}{mode}
Restart=always
RestartSec=5
TimeoutStopSec=15
KillMode=control-group
UMask=0077

[Install]
WantedBy=default.target
''', encoding='utf-8')
wants = unit_dir / 'default.target.wants'
wants.mkdir(exist_ok=True)
link = wants / unit.name
if not link.exists() and not link.is_symlink():
    link.symlink_to(Path('..') / unit.name)

# Preserve the desktop startup configuration, replacing only the prior cloud kiosk.
labwc = home / '.config/labwc/autostart'
if labwc.exists():
    text = labwc.read_text(encoding='utf-8')
    old_lines = [line for line in text.splitlines() if 'docusense-kiosk.sh' in line and not line.lstrip().startswith('#')]
    if old_lines:
        backup = labwc.with_name('autostart.before-adgvs-local')
        if not backup.exists():
            shutil.copy2(labwc, backup)
        for line in old_lines:
            text = text.replace(line, '# Previous cloud kiosk replaced by ADGVS local XDG autostart.')
        labwc.write_text(text, encoding='utf-8')

entry = f'''[Desktop Entry]
Type=Application
Name=ADGVS Paper Validation
Comment=Open the local camera paper validation station
Exec="{station / 'open-local.sh'}"
Path={repo}
Terminal=false
Icon=applications-office
StartupNotify=false
'''
autostart = home / '.config/autostart'
autostart.mkdir(parents=True, exist_ok=True)
(autostart / 'adgvs-local.desktop').write_text(entry, encoding='utf-8')
desktop = home / 'Desktop'
desktop.mkdir(exist_ok=True)
shortcut = desktop / 'ADGVS Paper Validation.desktop'
shortcut.write_text(entry.replace(f'Exec="{station / "open-local.sh"}"', f'Exec="{station / "open-local.sh"}" --window'), encoding='utf-8')
shortcut.chmod(0o755)
print('Installed: enabled user service, desktop autostart, and ADGVS Paper Validation shortcut.')
print('Mode: ' + ('DEMO database; real hardware moves only after Feed.' if args.demo else 'configured database'))
result = subprocess.run(['systemctl', '--user', 'daemon-reload'], capture_output=True, text=True)
if result.returncode:
    print('Current user bus unavailable. Configuration takes effect on the next desktop login/reboot.')
    print(result.stderr.strip())
else:
    print('User manager reloaded. Close any manually started copy before starting adgvs-local.service.')
