import re,sys,json,urllib.parse,urllib.request,html,time
def lookup(codes, sex='M', dob='01/01/1957'):
    q=[('v','6'),('tab','demo'),('from-tab','demo'),('f',sex),('dob',dob),('problems',','.join(codes)),('pdone','1')]
    url='https://imagic.nlm.nih.gov/imagic/?'+urllib.parse.urlencode(q)
    t=urllib.request.urlopen(url,timeout=30).read().decode()
    txt=html.unescape(re.sub(r'<[^>]*>',' ',t)); txt=re.sub(r'\s+',' ',txt)
    m=re.search(r'Optional refinement(.*?)(Submit Refinement|Back to Problem List)',txt)
    return url, (m.group(1).strip() if m else txt[-1500:])
if __name__=='__main__':
    for arg in sys.argv[1:]:
        u,r=lookup(arg.split('+')); print(arg,'=>',r[:700]); time.sleep(1)
