"""Run in customer-service after the live handoff scenario. Resolves the test handoff."""
import asyncio
import json
from uuid import uuid4
import httpx
from websockets.asyncio.client import connect


async def main():
    async with httpx.AsyncClient(timeout=10,trust_env=False) as client:
        async def token(user,role,project):
            r=await client.post('http://ecommerce:8001/api/v1/auth/demo-token',json={
                'user_id':user,'role':role,'welfare_project_id':project})
            r.raise_for_status();return r.json()['access_token']
        a=await token('user_002','customer','welfare_a')
        b=await token('user_003','customer','welfare_b')
        staff=await token('agent_001','agent','welfare_a')
        other=await token('agent_002','agent','welfare_b')
        headers={'Authorization':'Bearer '+staff}
        r=await client.get('http://customer-service:8000/api/v1/handoffs',headers=headers);r.raise_for_status()
        handoff=next(item for item in r.json() if item['user_id']=='user_002')
        base='http://customer-service:8000/api/v1/handoffs/'+handoff['id']
        r=await client.post(base+'/accept',headers={'Authorization':'Bearer '+other})
        assert r.status_code==404,r.text
        r=await client.post(base+'/accept',headers=headers);assert r.status_code==204,r.text
        async with connect('ws://customer-service:8000/api/v1/realtime') as wa, connect('ws://customer-service:8000/api/v1/realtime') as wb:
            for ws,t in [(wa,a),(wb,b)]:
                await ws.send(json.dumps({'type':'authenticate','token':t}))
                assert json.loads(await ws.recv())['event_type']=='connected'
            message_id='scope_check_'+uuid4().hex
            r=await client.post(base+'/messages',headers=headers,json={'message_id':message_id,'text':'Scoped realtime integration verification.'})
            assert r.status_code==204,r.text
            async with asyncio.timeout(10):
                while True:
                    event=json.loads(await wa.recv())
                    if event.get('event_data',{}).get('message_id')==message_id:break
            try:
                event=await asyncio.wait_for(wb.recv(),timeout=2)
                raise AssertionError('Unexpected cross-tenant message: '+event)
            except asyncio.TimeoutError:pass
        r=await client.post(base+'/resolve',headers=headers);assert r.status_code==204,r.text
        r=await client.post('http://customer-service:8000/api/v1/conversations/current',headers={'Authorization':'Bearer '+a})
        assert r.json()['mode']=='AI',r.text
        print('PASS staff ownership, cross-tenant denial, scoped Redis/WebSocket delivery, human reply and return to AI')


asyncio.run(main())
