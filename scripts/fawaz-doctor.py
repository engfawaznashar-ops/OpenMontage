"""Read-only, credential-safe local runtime inventory. No installs or provider calls."""
from pathlib import Path
import importlib.util,importlib.metadata,subprocess,sys,shutil,json,os
ROOT=Path(__file__).resolve().parents[1]
os.environ['PATH']=os.pathsep.join([str(ROOT/'.venv/Scripts'),str(ROOT/'remotion-composer/node_modules/@remotion/compositor-win32-x64-msvc'),os.environ.get('PATH','')])
def version(binary):
 p=shutil.which(binary)
 if not p:return {'found':False}
 try:
  r=subprocess.run([p,'-version' if binary in ('ffmpeg','ffprobe') else '--version'],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=8)
  return {'found':True,'exit_code':r.returncode,'version':(r.stdout or r.stderr).splitlines()[0][:200]}
 except Exception as e:return {'found':True,'error_type':type(e).__name__}
out={'root':str(ROOT),'python':sys.version.split()[0],'python_executable':sys.executable,'node':version('node'),'ffmpeg':version('ffmpeg'),'ffprobe':version('ffprobe'),'core_modules':{k:importlib.util.find_spec(k) is not None for k in ['yaml','requests','dotenv','numpy']},'remotion_cli_present':(ROOT/'remotion-composer/node_modules/@remotion/cli/remotion-cli.js').exists(),'provider_functionality':'not tested'}
cli=shutil.which('claude')
if not cli and os.name=='nt':
 choices=list((Path.home()/'AppData/Local/Claude-Data').glob('*/claude-code/*/*/claude.exe'))
 cli=str(max(choices,key=lambda p:p.stat().st_mtime)) if choices else None
out['claude_found']=bool(cli)
if cli:
 try:
  r=subprocess.run([cli,'auth','status'],capture_output=True,text=True,timeout=15,stdin=subprocess.DEVNULL)
  a=json.loads(r.stdout);out['claude_auth']={k:a.get(k) for k in ['loggedIn','authMethod','subscriptionType']}
 except Exception as e:out['claude_auth']={'error_type':type(e).__name__}
print(json.dumps(out,ensure_ascii=False,indent=2))
