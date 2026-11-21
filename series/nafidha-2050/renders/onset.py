# usage: onset.py <generated_clip.mp4> <reference_vo.(mp3|wav)>  -> seconds where reference starts inside clip audio
import sys, subprocess, numpy as np
def load(p):
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',p,'-ac','1','-ar','8000','-f','s16le','-'])
    return np.frombuffer(raw,dtype=np.int16).astype(np.float32)
a=load(sys.argv[1]); b=load(sys.argv[2])
ea=np.abs(a); eb=np.abs(b)
n=1<<int(np.ceil(np.log2(len(ea)+len(eb))))
c=np.fft.irfft(np.fft.rfft(ea,n)*np.conj(np.fft.rfft(eb,n)),n)
lag=int(np.argmax(c[:len(ea)])); print(f"{lag/8000:.3f}")
