"""Optional Chrome headless UI smoke test, using a separate disposable profile."""
import base64
import json
from pathlib import Path
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
import websocket

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'tmp/ui-review'
OUT.mkdir(parents=True,exist_ok=True)
browser=Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
process=subprocess.Popen([str(browser),'--headless=new','--no-first-run','--no-default-browser-check',
    '--disable-gpu','--no-proxy-server','--remote-debugging-port=9237','--remote-allow-origins=http://localhost:9237',
    '--window-size=1440,1100',f'--user-data-dir={OUT / "browser-test-profile"}','about:blank'],
    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
opener=build_opener(ProxyHandler({}))
ws=None
sequence=0
errors=[]


def rpc(method,params=None):
    global sequence
    sequence+=1;ident=sequence
    ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
    while True:
        message=json.loads(ws.recv())
        if message.get('method')=='Runtime.exceptionThrown':errors.append(message['params'])
        if message.get('id')==ident:
            if 'error' in message:raise RuntimeError(message['error'])
            return message.get('result',{})


def evaluate(expression):
    response=rpc('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True})
    if response.get('exceptionDetails'):raise RuntimeError(response['exceptionDetails'])
    return response.get('result',{}).get('value')


def wait(expression):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if evaluate(expression):return
        time.sleep(.2)
    raise AssertionError('UI timeout: '+expression+'\n'+str(evaluate('[location.href, document.readyState, document.documentElement.outerHTML.slice(0,1500)]'))+'\n'+str(errors))


def select(label,value):
    evaluate(f"(()=>{{const s=document.querySelector('select[aria-label={json.dumps(label)}]');s.value={json.dumps(value)};s.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")


def nav(label):
    evaluate(f"[...document.querySelectorAll('nav button')].find(b=>b.textContent.trim()==={json.dumps(label)}).click()")


def screenshot(name):
    image=rpc('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
    (OUT/(name+'.png')).write_bytes(base64.b64decode(image['data']))


try:
    tabs=None
    for _ in range(40):
        try:
            with opener.open('http://127.0.0.1:9237/json/list',timeout=1) as response:tabs=json.load(response)
            if tabs:break
        except Exception:pass
        time.sleep(.2)
    assert tabs,'Chrome did not start'
    ws=websocket.create_connection(tabs[0]['webSocketDebuggerUrl'],origin='http://localhost:9237',timeout=15)
    rpc('Runtime.enable');rpc('Page.enable');rpc('Network.enable')
    rpc('Network.setBlockedURLs',{'urls':['*fonts.googleapis.com*','*fonts.gstatic.com*']})
    for origin in ('http://127.0.0.1:5173','http://127.0.0.1:5174'):
        rpc('Storage.clearDataForOrigin',{'origin':origin,'storageTypes':'all'})
    rpc('Page.navigate',{'url':'http://127.0.0.1:5173/points'})
    wait("document.querySelectorAll('.points-stats strong').length===2")
    select('切换体验账号','user_002')
    wait("document.querySelector('.points-stats strong')?.textContent.trim()==='8000'")
    select('切换福利项目','birthday_a')
    wait("document.querySelector('.points-stats strong')?.textContent.trim()==='520'")
    screenshot('final-birthday-points')
    nav('商城')
    wait("document.querySelectorAll('.mall-card').length===1 && document.body.innerText.includes('生日蛋糕礼盒')")
    evaluate("document.querySelector('.mall-card-footer button:last-child').click()")
    wait("document.querySelector('.redemption .quote')?.innerText.includes('520')")
    screenshot('final-redemption')
    print('PASS employee/project switch, birthday product pool, quote and confirmation UI',flush=True)
    rpc('Page.navigate',{'url':'http://127.0.0.1:5174'})
    wait("document.querySelectorAll('.metrics article').length===5")
    nav('政策知识库')
    wait("document.querySelectorAll('.documents tbody tr').length>10")
    assert not evaluate("document.body.innerText.includes('悦享季度激励积分有效期')")
    screenshot('final-policy-library')
    nav('订单退分')
    wait("document.querySelector('.operations-panel tbody')?.innerText.includes('ORDER_A_003')")
    screenshot('final-refund-workbench')
    select('切换后台账号','admin_002')
    wait("document.querySelector('select[aria-label=\"切换福利项目\"]')?.value==='welfare_b' && document.querySelectorAll('.metrics article').length===5")
    assert not evaluate("document.body.innerText.includes('ORDER_A_001')")
    screenshot('final-tenant-b-admin')
    assert not errors,errors
    print('PASS policy browsing, scoped refund UI, tenant switch and no uncaught JS exceptions',flush=True)
finally:
    if ws:
        try:rpc('Browser.close')
        except Exception:pass
        ws.close()
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:process.terminate()
