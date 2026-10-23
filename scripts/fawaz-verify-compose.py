"""Verify the existing three-second OpenMontage diagnostic through VideoCompose.
No provider generation, dependency installs, credentials, or production checkpoints.
Run using scripts/fawaz-python.cmd scripts/fawaz-verify-compose.py
"""
from pathlib import Path
import argparse,json,subprocess,sys,os,time,shutil
ROOT=Path(__file__).resolve().parents[1]
os.environ['PATH']=os.pathsep.join([str(ROOT/'.venv/Scripts'),str(ROOT/'remotion-composer/node_modules/@remotion/compositor-win32-x64-msvc'),os.environ.get('PATH','')])
sys.path.insert(0,str(ROOT))
P=argparse.ArgumentParser()
P.add_argument('--input',type=Path,default=ROOT/'docs/fawaz-openmontage-audit/render-input.json')
P.add_argument('--worker',action='store_true')
args=P.parse_args()
AUDIT=ROOT/'docs/fawaz-openmontage-verification'
AUDIT.mkdir(exist_ok=True)
out=ROOT/'projects/makalmat-tawzeef-ai-employees/renders/integration_claude_verified_3s.mp4'
if args.worker:
 from tools.video.video_compose import VideoCompose
 data=json.loads(args.input.read_text(encoding='utf-8-sig'))
 data['output_path']=str(out)
 result=VideoCompose().execute(data)
 receipt={'success':bool(result.success),'error':getattr(result,'error',None),'output_path':str(out),'tool':'video_compose','result':vars(result)}
 (AUDIT/'tool-result.json').write_text(json.dumps(receipt,default=str,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'tool_success':receipt['success'],'error':receipt['error']}),flush=True)
 raise SystemExit(0 if result.success else 2)
start=time.time()
cmd=[sys.executable,str(Path(__file__).resolve()),'--worker','--input',str(args.input.resolve())]
with (AUDIT/'compose.log').open('w',encoding='utf-8') as log:
 proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,encoding='utf-8')
 try:rc=proc.wait(timeout=180)
 except subprocess.TimeoutExpired:
  if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
  else:proc.kill()
  raise SystemExit('FAIL: compose exceeded 180 seconds; process tree terminated')
checks={'compose_exit_code':rc,'duration_seconds':round(time.time()-start,2),'output_exists':out.exists(),'output_bytes':out.stat().st_size if out.exists() else 0}
if rc==0 and out.exists():
 r=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(out)],capture_output=True,text=True,encoding='utf-8',timeout=30)
 checks['ffprobe_exit_code']=r.returncode
 if r.returncode==0:
  probe=json.loads(r.stdout);(AUDIT/'ffprobe.json').write_text(json.dumps(probe,indent=2),encoding='utf-8')
  checks['streams']=[{'type':s.get('codec_type'),'codec':s.get('codec_name'),'width':s.get('width'),'height':s.get('height'),'fps':s.get('r_frame_rate')} for s in probe['streams']]
  checks['media_duration_seconds']=float(probe['format']['duration'])
  checks['has_audio']=any(s.get('codec_type')=='audio' for s in probe['streams'])
  checks['has_video']=any(s.get('codec_type')=='video' for s in probe['streams'])
  checks['duration_matches_sample']=abs(checks['media_duration_seconds']-3)<0.2
 dec=subprocess.run(['ffmpeg','-v','error','-i',str(out),'-c:v','rawvideo','-c:a','pcm_s16le','-f','null','-'],capture_output=True,timeout=30)
 checks['decode_exit_code']=dec.returncode
 (AUDIT/'decode-stderr.txt').write_bytes(dec.stderr)
checks['passed']=rc==0 and checks.get('ffprobe_exit_code')==0 and checks.get('has_audio') and checks.get('has_video') and checks.get('duration_matches_sample') and checks.get('decode_exit_code')==0
checks['scope']='Existing local composition diagnostic only; no provider generation or full episode approval.'
(AUDIT/'verification.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
print(json.dumps(checks),flush=True)
raise SystemExit(0 if checks['passed'] else 1)
