import glob,sys
from playwright.sync_api import sync_playwright
CSS="*{box-sizing:border-box}body{margin:0}p,h1,h2,h3{margin:0}ul{margin:0;padding-left:34px}x-icon,x-shape{display:inline-block;flex:none}x-connector{display:none}table{border-collapse:collapse;width:100%}td,th{padding:0.35em 0.6em;vertical-align:top}"
JS="""() => {const s=document.querySelector('section');let mb=0,ft=9999,bad=[];
for(const c of s.children){const cs=getComputedStyle(c);const r=c.getBoundingClientRect();
 if(cs.position==='absolute'){ if(cs.bottom!=='auto' && c.tagName==='DIV' && r.top>800) ft=Math.min(ft,r.top); continue;}
 if(c.tagName==='ASIDE')continue; mb=Math.max(mb,r.bottom);}
for(const e of s.querySelectorAll('div,p,li,td,th,h2,h3')){ if(e.scrollWidth>e.clientWidth+3 && getComputedStyle(e).overflow!=='visible') bad.push('W:'+e.textContent.slice(0,40));
 if(e.tagName==='DIV' && e.style.height && e.scrollHeight>e.clientHeight+3) bad.push('H:'+e.textContent.trim().slice(0,40));}
for(const e of s.querySelectorAll('p')){ if(e.scrollWidth>e.clientWidth+3) bad.push('W:'+e.textContent.slice(0,40));}
return [Math.round(mb),Math.round(ft),bad.slice(0,4)];}"""
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={'width':1920,'height':1080})
    for f in sorted(glob.glob(sys.argv[1]+'/*.html')):
        sec=open(f).read().replace('<section ','<section data-x="1" ',1)
        sec=sec.replace('style="background','style="position:relative;width:1920px;height:1080px;box-sizing:border-box;overflow:visible;background',1)
        sec=sec.split('<aside>')[0]+'</section>'
        pg.set_content('<html><head><style>'+CSS+'</style></head><body>'+sec+'</body></html>')
        mb,ft,bad=pg.evaluate(JS)
        flag='OVER' if mb>min(ft-8,950) else 'ok'
        print(f.split('/')[-1],mb,ft,flag,bad)
    b.close()
