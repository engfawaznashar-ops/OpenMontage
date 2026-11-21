# usage: python3 deliver.py <queue-job.json> <image-url>  -> writes PNG at job size to job["out"]
import json, sys, io, urllib.request
from PIL import Image
job = json.load(open(sys.argv[1]))
data = urllib.request.urlopen(sys.argv[2]).read()
im = Image.open(io.BytesIO(data)).convert('RGB')
W, H = job['width'], job['height']
# cover-fit to exact size (aspect already matches; this only normalises pixels)
r = max(W / im.width, H / im.height)
im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
l, t = (im.width - W) // 2, (im.height - H) // 2
im = im.crop((l, t, l + W, t + H))
tmp = job['out'] + '.part.png'; im.save(tmp, 'PNG')
import os; os.replace(tmp, job['out']); print('delivered', job['out'], im.size)
