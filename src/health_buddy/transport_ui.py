"""Small local auth shells, never a health snapshot or credential cache."""

from starlette.responses import Response

from health_buddy.transport_security import SAFE_HEADERS

_STYLE = """
body {
  font: 17px system-ui; max-width: 42rem; margin: 3rem auto; padding: 0 1rem;
  color: #182d33; background: #f5f8f7;
}
label, button, textarea, input, select { display: block; margin: 1rem 0; }
textarea, input, button, select {
  font: inherit; max-width: 100%; box-sizing: border-box;
}
textarea, input, select { width: 100%; padding: .65rem; }
button { padding: .6rem 1rem; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
[hidden] { display: none; }
a { color: #165c63; }
"""
_HEAD = (
    '<!doctype html><html lang="en"><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Health Buddy access</title><style>'
    + _STYLE
    + '</style><body><main>'
)
_LOGIN = """<h1>Open your health workspace</h1>
<p>Use your private owner credential, a one-time local setup handoff, or the
configured private proxy. Credentials stay in this page only while you sign in.</p>
<label for="method">Sign-in method</label>
<select id="method">
  <option value="bearer">Owner credential</option>
  <option value="bootstrap">One-time setup handoff</option>
  <option value="proxy">Private proxy identity</option>
</select>
<label for="proof">Private credential or setup JSON</label>
<textarea id="proof" rows="4" autocomplete="off" spellcheck="false"></textarea>
<button id="signin" type="button">Sign in</button>
<button id="clear" type="button">Clear expired sign-in</button>
<section id="owner-retention" hidden>
  <h2>Save your private owner credential</h2>
  <p>Save this credential in your password manager or a private owner-only file
  before continuing. You need it for future sign-in after logout or expiry.
  This page does not save it for you. Keep it out of chats, shared links and logs.</p>
  <textarea id="owner-credential" rows="3" readonly
    autocomplete="off" spellcheck="false"></textarea>
  <button id="owner-continue" type="button">I saved it privately — continue</button>
  <button id="owner-hide" type="button">Hide credential without continuing</button>
</section>
<p><a id="open-workspace" href="/" hidden>Open workspace</a></p>
<p>Clearing sign-in keeps saved drafts and original pending requests. If your only
owner credential was lost, use deliberate local owner recovery.</p>
"""
_OWNER = """<h1>Connect a phone</h1>
<p>Prepare a connection, then reveal its private handoff only when your phone is
ready. The handoff expires after five minutes. A lost token response requires
revoking the uncertain device and preparing a new connection.</p>
<label for="name">Device label</label>
<input id="name" maxlength="80" value="My phone">
<label for="replacement">Same phone with a lost or replaced token (optional)</label>
<select id="replacement"><option value="">New phone</option></select>
<p>Refresh devices below, then deliberately choose its existing device reference
to re-pair the same phone. A different phone must use New phone.</p>
<button id="prepare" type="button">Prepare connection</button>
<label for="intent">Connection reference</label>
<input id="intent" maxlength="128" autocomplete="off">
<button id="status" type="button">Check status</button>
<button id="handoff" type="button">Reveal private phone handoff once</button>
<pre id="safe-result"></pre>
<section id="private-handoff" hidden>
  <h2>Private phone handoff</h2>
  <p>Transfer this code directly to your phone. Keep it out of chats, shared links
  and logs. This page never receives the phone's resulting upload token.</p>
  <textarea id="handoff-code" rows="6" readonly
    autocomplete="off" spellcheck="false"></textarea>
  <button id="hide-proof" type="button">Hide handoff</button>
</section>
<h2>Connected devices</h2>
<button id="devices" type="button">Refresh devices</button>
<pre id="device-list"></pre>
<label for="device">Device reference to revoke</label>
<input id="device" maxlength="128">
<button id="revoke" type="button">Revoke selected device</button>
<p><a href="/">Back to workspace</a></p>
<button id="clear" type="button">Sign out</button>
"""
_TAIL = (
    '<p id="message" role="status" aria-live="polite"></p>'
    '<noscript>JavaScript is needed for this protected sign-in and private handoff. '
    'Local owner commands remain available.</noscript></main>'
    '<script src="/auth.js"></script></body></html>'
)

SCRIPT = r"""'use strict';
(() => {
  const node = id => document.getElementById(id);
  const status = text => { node('message').textContent = text; };
  const identityKeys = ['installationId','datasetId','restoreEpoch'];
  let current = null;
  let ownerCredential = null;
  const pairing = new URLSearchParams(location.search).get('pairing');
  const safePairing = pairing && /^[A-Za-z0-9_.:-]{1,128}$/.test(pairing)
    ? pairing : null;
  if (node('intent') && safePairing) node('intent').value = safePairing;
  const destination = safePairing
    ? '/security?pairing='+encodeURIComponent(safePairing) : '/';
  if (node('open-workspace')) {
    node('open-workspace').href = destination;
    node('open-workspace').textContent = safePairing
      ? 'Open phone connection' : 'Open workspace';
  }
  async function call(path, method='GET', body=null, extras={}) {
    const headers = {'X-Health-Buddy-Browser':'1', ...extras};
    if (body !== null) headers['Content-Type']='application/json';
    if (current?.secret?.kind==='csrf')
      headers['X-CSRF-Token']=current.secret.value;
    if (current?.meta) for (const [i,key] of identityKeys.entries()) {
      const name = ['X-Installation-ID','X-Dataset-ID','X-Restore-Epoch'][i];
      headers[name]=current.meta[key];
    }
    const response = await fetch(path, {
      method, headers, body:body===null?undefined:JSON.stringify(body),
      credentials:'same-origin', cache:'no-store', redirect:'error'
    });
    const value = await response.json().catch(()=>null);
    if (!response.ok) throw new Error(
      /^[a-z_]{1,80}$/.test(value?.error?.code||'')
        ? value.error.code : 'request_failed'
    );
    if (!value || typeof value.data!=='object' || !value.meta)
      throw new Error('unverified_response');
    return value;
  }
  async function session() {
    current=await call('/v1/session');
    if (current.secret?.kind!=='csrf') throw new Error('owner_session_required');
    return current;
  }
  const action = (id, handler) => node(id)?.addEventListener('click',async()=>{
    const button=node(id);
    button.disabled=true;
    status('Working…');
    try { await handler(); }
    catch(e) {
      const code=/^[a-z_]{1,80}$/.test(e.message||'')
        ? e.message : 'request_failed';
      status('Not completed ('+code+'). Original drafts and requests are retained.');
    } finally { button.disabled=false; }
  });
  function hideOwnerCredential() {
    ownerCredential=null;
    if (node('owner-credential')) node('owner-credential').value='';
    if (node('owner-retention')) node('owner-retention').hidden=true;
  }
  async function finishSignIn(bearer) {
    await call('/v1/sessions','POST',{},
      bearer?{Authorization:'Bearer '+bearer}:{});
    await session();
    hideOwnerCredential();
    location.replace(destination);
  }
  action('owner-continue',async()=>{
    if (!ownerCredential) throw new Error('owner_credential_unavailable');
    await finishSignIn(ownerCredential);
  });
  action('owner-hide',async()=>{
    hideOwnerCredential();
    status('Credential hidden. Use your privately saved credential to sign in. '+
      'If it was not saved, deliberate local owner recovery is required.');
  });
  action('signin',async()=>{
    if (ownerCredential) throw new Error('save_owner_credential_first');
    const mode=node('method').value;
    let proof=node('proof').value.trim();
    node('proof').value='';
    let bearer=null;
    try {
      if (mode==='bootstrap') {
        const input=JSON.parse(proof);
        proof='';
        if (input.protocolVersion!==1 || !input.identity ||
            Object.keys(input).sort().join('|')!=='identity|proof|protocolVersion')
          throw new Error('invalid_setup_handoff');
        const headers={};
        identityKeys.forEach((key,i)=>{
          const name=['X-Installation-ID','X-Dataset-ID','X-Restore-Epoch'][i];
          headers[name]=input.identity[key];
        });
        const result=await call('/v1/bootstrap','POST',{proof:input.proof},headers);
        input.proof='';
        if (result.secret?.kind!=='owner-token')
          throw new Error('unverified_response');
        ownerCredential=result.secret.value;
        result.secret.value='';
        node('owner-credential').value=ownerCredential;
        node('owner-retention').hidden=false;
        status('Setup handoff used. Save the private owner credential '+
          'before continuing.');
        return;
      } else if (mode==='bearer') bearer=proof;
      else if (mode!=='proxy') throw new Error('invalid_method');
      proof='';
      await finishSignIn(bearer);
    } finally { proof=''; bearer=null; }
  });
  action('clear',async()=>{
    try { await session(); } catch(e) { current=null; }
    await call('/v1/session','DELETE');
    current=null;
    hideOwnerCredential();
    if (node('open-workspace')) node('open-workspace').hidden=true;
    if (node('handoff-code')) node('handoff-code').value='';
    if (node('private-handoff')) node('private-handoff').hidden=true;
    status('Sign-in cleared. Drafts and original pending requests are retained.');
    if (!node('signin')) location.replace('/login');
  });
  const reference = id => {
    const value=node(id).value.trim();
    if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(value))
      throw new Error('invalid_reference');
    return value;
  };
  action('prepare',async()=>{
    await session();
    const payload={name:node('name').value};
    if (node('replacement').value)
      payload.replacementDeviceId=reference('replacement');
    const result=await call('/v1/pairing-intents','POST',payload);
    node('safe-result').textContent=JSON.stringify(result.data,null,2);
    const id=result.data.id;
    if (typeof id==='string') node('intent').value=id;
    status('Connection prepared. Reveal its private handoff only '+
      'when your phone is ready.');
  });
  action('status',async()=>{
    await session();
    const result=await call('/v1/pairing-intents/'+reference('intent'));
    node('safe-result').textContent=JSON.stringify(result.data,null,2);
    status('Status refreshed.');
  });
  action('handoff',async()=>{
    await session();
    const result=await call('/v1/pairing-intents/'+reference('intent')+
      '/handoff','POST',{});
    if (result.secret?.kind!=='pairing-proof')
      throw new Error('unverified_response');
    node('handoff-code').value=JSON.stringify({
      endpoint:location.origin, identity:result.data.identity,
      protocolVersion:result.data.protocolVersion, proof:result.secret.value
    });
    result.secret.value='';
    node('private-handoff').hidden=false;
    status('Private handoff revealed once. It is not saved in this browser.');
  });
  action('hide-proof',async()=>{
    node('handoff-code').value='';
    node('private-handoff').hidden=true;
    status('Handoff hidden. Reveal is one-time; create a new connection if needed.');
  });
  action('devices',async()=>{
    await session();
    const result=await call('/v1/devices');
    node('device-list').textContent=JSON.stringify(result.data,null,2);
    const select=node('replacement');
    select.replaceChildren(new Option('New phone',''));
    if (!Array.isArray(result.data.items)) throw new Error('unverified_response');
    for (const device of result.data.items) {
      if (typeof device.id!=='string' ||
          !/^[A-Za-z0-9_.:-]{1,128}$/.test(device.id) ||
          typeof device.name!=='string') throw new Error('unverified_response');
      select.add(new Option(device.name+' — '+device.id+
        (device.active?'':' (revoked)'),device.id));
    }
    status('Device inventory refreshed. Select an existing reference only '+
      'to re-pair that same phone.');
  });
  action('revoke',async()=>{
    await session();
    await call('/v1/devices/'+reference('device'),'DELETE');
    status('Selected device revoked.');
  });
  if (node('signin')) session().then(()=>{
    node('open-workspace').hidden=false;
    status('Already signed in. Open the workspace, or clear sign-in '+
      'to switch credentials.');
  }).catch(()=>{
    current=null;
    status('Sign in to continue. If a previous sign-in expired, clear it first.');
  });
  window.addEventListener('pagehide',()=>{
    current=null;
    hideOwnerCredential();
    if (node('proof')) node('proof').value='';
    if (node('handoff-code')) node('handoff-code').value='';
  });
})();
"""


def shell(owner: bool = False) -> Response:
    headers = dict(SAFE_HEADERS)
    headers["content-security-policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'none'"
    )
    return Response(
        _HEAD + (_OWNER if owner else _LOGIN) + _TAIL,
        media_type="text/html",
        headers=headers,
    )


def script() -> Response:
    return Response(SCRIPT, media_type="text/javascript", headers=SAFE_HEADERS)
