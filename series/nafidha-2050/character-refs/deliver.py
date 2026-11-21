# usage: python3 deliver.py <queue-job.json> <image-url>  -> writes PNG at job size to job["out"]
import json, sys, io, urllib.request
from PIL import Image
job = json.load(open(sys.argv[1]))
data = urllib.request.urlopen(sys.argv[2]).read()
im = Image.open(io.BytesIO(data)).convert('RGB')
W, H = job['width'], job['height']
# cover-fit to exact size (aspect already matches; this only normalises pixels)
if abs(im.width / im.height - W / H) < 0.02:
    im = im.resize((W, H), Image.LANCZOS)
else:  # aspect differs (provider snapped the ratio): contain and pad with white, never crop the subject
    r = min(W / im.width, H / im.height)
    im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    bg = Image.new('RGB', (W, H), (255, 255, 255)); bg.paste(im, ((W - im.width) // 2, (H - im.height) // 2)); im = bg
tmp = job['out'] + '.part.png'; im.save(tmp, 'PNG')
import os; os.replace(tmp, job['out']); print('delivered', job['out'], im.size)
