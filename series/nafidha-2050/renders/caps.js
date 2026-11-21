const {chromium}=require('playwright'); const fs=require('fs');
const P='/home/user/OpenMontage/projects/mustashrif-2050'; const states=JSON.parse(fs.readFileSync(P+'/renders/caps/states.json'));
const FD='/tmp/claude-0/-home-user/e28796f1-9478-582b-8b70-34cc02e82ed7/scratchpad/fonts/node_modules/@fontsource';
const css=`@font-face{font-family:L;src:url(file://${FD}/lalezar/files/lalezar-arabic-400-normal.woff2)}
@font-face{font-family:A;font-weight:800;src:url(file://${FD}/almarai/files/almarai-arabic-800-normal.woff2)}
html,body{margin:0;background:transparent;width:1080px;height:1920px;overflow:hidden}
.cap{position:absolute;left:90px;right:90px;bottom:520px;direction:rtl;text-align:center;font-family:L;font-size:96px;line-height:1.15;color:#F3E6D5;
 text-shadow:0 0 18px rgba(0,0,0,.55),6px 6px 0 #0D0804,-2px -2px 0 #0D0804,2px -2px 0 #0D0804,-2px 2px 0 #0D0804}
.w{display:inline-block;transition:none;margin:0 6px}
.on{color:#29E6FF;transform:scale(1.12);text-shadow:0 0 26px rgba(41,230,255,.75),6px 6px 0 #0D0804}
.end{position:absolute;left:0;right:0;direction:rtl;text-align:center}
.s{font-family:A;font-weight:800;font-size:58px;color:#29E6FF;text-shadow:4px 4px 0 #0D0804}
.b{font-family:L;font-size:150px;color:#F3E6D5;text-shadow:7px 7px 0 #0D0804;line-height:1}
.q{color:#29E6FF;text-shadow:0 0 30px rgba(41,230,255,.8),7px 7px 0 #0D0804}`;
(async()=>{const b=await chromium.launch(); const p=await b.newPage({viewport:{width:1080,height:1920}});
fs.writeFileSync(P+'/renders/caps/page.html',`<html><head><meta charset="utf-8"><style>${css}</style></head><body><div id=r></div></body></html>`); await p.goto('file://'+P+'/renders/caps/page.html');
await p.evaluate(async()=>{await document.fonts.load('96px L'); await document.fonts.load('800 58px A');});
for(const s of states){
  await p.evaluate(s=>{const r=document.getElementById('r');
    if(s.type==='cap'){r.innerHTML='<div class=cap>'+s.words.map((w,i)=>`<span class="w${i===s.on?' on':''}">${w}</span>`).join(' ')+'</div>';}
    else if(s.type==='end'){r.innerHTML=`<div class=end style="top:700px"><div class=s>الحلقة الجاية</div><div class=b style="margin-top:30px">نسختك الرقمية</div><div class="b q" style="margin-top:20px">أشطر منك؟</div><div class=s style="margin-top:60px;color:#F3E6D5">تابع نافذة ٢٠٥٠</div></div>`;}
    else r.innerHTML='';},s);
  await p.waitForTimeout(30);
  await p.screenshot({path:`${P}/renders/caps/${s.id}.png`,omitBackground:true});
}
await b.close();})();
