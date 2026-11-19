#!/usr/bin/env bash
# Install/update atomically. Starting the microphone service is an explicit --start action.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
service_user="${SUDO_USER:-$USER}"
destination=/etc/systemd/system
start_service=false
while (($#)); do
  case "$1" in
    --user) service_user="${2:?--user requires a value}"; shift 2 ;;
    --dest) destination="${2:?--dest requires a value}"; shift 2 ;;
    --start) start_service=true; shift ;;
    *) echo 'Usage: install_service.sh [--user USER] [--dest DIRECTORY] [--start]' >&2; exit 2 ;;
  esac
done
python_bin="${LAYA_PYTHON:-$project_root/../.env_laya/bin/python}"
service_group="$(id -gn "$service_user")"
mkdir -p -- "$destination"
"$python_bin" - "$project_root" "$python_bin" "$service_user" "$service_group" "$destination" <<'PY'
import os,sys,tempfile
from pathlib import Path
root,python,user,group,destination=sys.argv[1:]
def quote(value):
    if '\n' in value or '\r' in value:raise ValueError('Invalid systemd value')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
source=Path(root,'systemd/hermina-fraud-detector.service').read_text().splitlines()
replacements={'User':user,'Group':group,'WorkingDirectory':root.replace('\\',r'\x5c').replace(' ',r'\x20').replace('%','%%'),
 'EnvironmentFile':'-'+(root+'/config/service.env').replace('\\',r'\x5c').replace(' ',r'\x20').replace('%','%%'),'ExecStart':quote(root+'/scripts/run_standalone.sh')}
source.insert(source.index('[Service]')+1,'Environment='+quote('LAYA_PYTHON='+python))
result='\n'.join(k+'='+replacements[k] if '=' in line and (k:=line.split('=',1)[0]) in replacements else line for line in source)+'\n'
path=Path(destination,'hermina-fraud-detector.service')
if path.exists() and path.read_text()==result:
    print('Service unchanged:',path)
else:
    if path.exists():Path(str(path)+'.bak').write_text(path.read_text())
    fd,tmp=tempfile.mkstemp(dir=destination,prefix='.laya-service-')
    with os.fdopen(fd,'w') as f:f.write(result)
    os.chmod(tmp,0o644);os.replace(tmp,path);print('Service installed:',path)
PY
if [[ "$destination" == /etc/systemd/system ]]; then
  systemctl daemon-reload
  if "$start_service"; then systemctl enable --now hermina-fraud-detector.service; fi
elif "$start_service"; then
  echo '--start requires /etc/systemd/system destination' >&2; exit 2
fi
