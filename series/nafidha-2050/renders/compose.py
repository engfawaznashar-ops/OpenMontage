import json, subprocess, os
P='/home/user/OpenMontage/projects/mustashrif-2050'; V=f'{P}/assets/video'; A=f'{P}/assets/audio/vo'; R=f'{P}/renders'
SC='/tmp/claude-0/-home-user/e28796f1-9478-582b-8b70-34cc02e82ed7/scratchpad'
tl=json.load(open(f'{P}/edit_timeline.json')); T=tl['total']+2.5; FPS=30
st={(c['seg'],c['cut']):c for c in tl['cuts']}
def onset(clip,ref): return float(subprocess.check_output(['python3',f'{SC}/onset.py',clip,ref]))
# talking clips: (clip, vo-ref, timeline start of segment cut, first line start)
talk={'E01-01':('E01-01-talk.mp4','vo-E01-01.mp3',('E01-01',2),[('E01-01',2),('E01-01',3)]),
      'E01-02':('E01-02-talk.mp4','vo-E01-02.mp3',('E01-02',2),[('E01-02',2)]),
      'E01-05':('E01-05-talk.mp4','vo-E01-05.mp3',('E01-05',2),[('E01-05',2),('E01-05',3)]),
      'E01-06':('E01-06-talk.mp4','vo-E01-06.mp3',('E01-06',2),[('E01-06',2)])}
motion={('E01-01',1):'E01-01-c1.mp4',('E01-02',1):'E01-02-c1.mp4',('E01-03',1):'E01-03-c1.mp4',('E01-03',2):'E01-03-c2.mp4',('E01-03',3):'E01-03-c3.mp4',
        ('E01-04',1):'E01-04-c1.mp4',('E01-04',2):'E01-04-c2.mp4',('E01-05',1):'E01-05-c1.mp4',('E01-06',1):'E01-06-c1.mp4'}
os.makedirs(f'{R}/v',exist_ok=True); parts=[]
SCALE="scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,fps=30,format=yuv420p,setsar=1"
def cut(src,ss,d,out):
    subprocess.run(['ffmpeg','-v','error','-y','-ss',f'{ss:.3f}','-i',src,'-t',f'{d:.3f}','-vf',SCALE,'-an','-c:v','libx264','-preset','veryfast','-crf','17',out],check=True)
for c in tl['cuts']:
    k=(c['seg'],c['cut'])
    if k in motion:
        src=f"{V}/{motion[k]}"; out=f"{R}/v/{c['seg']}-{c['cut']}.mp4"; cut(src,0.0,c['dur'],out); parts.append(out)
    elif c['seg'] in talk and k==talk[c['seg']][2]:
        clip,ref,_,cuts=talk[c['seg']]; d=sum(st[x]['dur'] for x in cuts)
        lead=c['lines'][0]['start']-c['start']   # first line offset inside the cut
        on=onset(f'{V}/{clip}',f'{A}/{ref}'); out=f"{R}/v/{c['seg']}-talk.mp4"; cut(f'{V}/{clip}',max(on-lead,0),d,out); parts.append(out)
        print(c['seg'],'onset',on)
# end card: tail of the last talking clip, darkened + blurred (still moving)
clip,ref,_,_=talk['E01-06']; on=onset(f'{V}/{clip}',f'{A}/{ref}')
subprocess.run(['ffmpeg','-v','error','-y','-ss',f'{on+3.6:.3f}','-i',f'{V}/{clip}','-t','2.5','-vf',SCALE+',boxblur=14:2,eq=brightness=-0.22','-an','-c:v','libx264','-preset','veryfast','-crf','17',f'{R}/v/endcard.mp4'],check=True)
parts.append(f'{R}/v/endcard.mp4')
open(f'{R}/v/list.txt','w').write('\n'.join(f"file '{p}'" for p in parts))
subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',f'{R}/v/list.txt','-c','copy',f'{R}/v/base.mp4'],check=True)
# PiP: narrator in round window over the future scenes (E01-03 c1 .. E01-04 c2)
t0=st[('E01-03',1)]['start']; t1=st[('E01-04',2)]['start']+st[('E01-04',2)]['dur']
pon=onset(f'{V}/PIP-talk.mp4',f'{A}/vo-PIP-E01-03-04.mp3'); print('pip onset',pon)
S=420; X=1080-S-40; Y=250
fc=(f"[1:v]trim=start={pon:.3f}:duration={t1-t0:.3f},setpts=PTS-STARTPTS+{t0:.3f}/TB,crop=iw*0.9:iw*0.9:iw*0.05:ih*0.06,scale={S}:{S},format=rgba[pv];"
    f"[2:v]format=gray,scale={S}:{S}[m];[pv][m]alphamerge[pc];"
    f"[0:v][pc]overlay={X}:{Y}:enable='between(t,{t0:.3f},{t1:.3f})'[b1];"
    f"[b1][3:v]overlay={X}:{Y}:enable='between(t,{t0:.3f},{t1:.3f})'[b2];"
    f"[b2][4:v]overlay=0:0:format=auto,format=yuv420p[v]")
subprocess.run(['ffmpeg','-v','error','-y','-i',f'{R}/v/base.mp4','-i',f'{V}/PIP-talk.mp4','-loop','1','-i',f'{R}/pip-mask.png','-loop','1','-i',f'{R}/pip-ring.png',
  '-i',f'{R}/caps/caps.mov','-i',f'{P}/assets/audio/E01-mix.wav','-filter_complex',fc,'-map','[v]','-map','5:a','-t',f'{T:.2f}',
  '-c:v','libx264','-preset','medium','-crf','18','-c:a','aac','-b:a','192k','-movflags','+faststart',f'{R}/E01-final-9x16.mp4'],check=True)
print('done')
