async function api(path, opts={}) {
  const res = await fetch(path, Object.assign({headers: {'Content-Type': 'application/json'}}, opts));
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function fmt(t) { return (t||0).toFixed(2); }

function setRec(on){
  const d = document.getElementById('rec');
  if(on) d.classList.add('on'); else d.classList.remove('on');
}

async function refresh() {
  try {
    const s = await api('/status');
    document.getElementById('srvtime').textContent = s.server_time;
    document.getElementById('elapsed').textContent = fmt(s.elapsed);
    document.getElementById('count').textContent = s.count;
    document.getElementById('status').textContent = ' · Grabber: ' + s.grabber_status;
    setRec(!!s.recording);
    renderList(s.annotations);
  } catch(e) { console.error(e); }
}

function renderList(arr) {
  const root = document.getElementById('list');
  root.innerHTML = '';
  (arr||[]).forEach(item => {
    const div = document.createElement('div');
    div.className = 'ann';
    const tt = document.createElement('time');
    tt.textContent = '[' + fmt(item.t) + 's]';
    const span = document.createElement('span');
    span.textContent = ' ' + item.text;
    div.appendChild(tt); div.appendChild(span);
    root.appendChild(div);
  });
}

async function start() {
  await api('/start', {method:'POST'});
  refresh();
}

async function stopAll() {
  const r = await api('/stop', {method:'POST'});
  alert('Saved and quitting.\nJSON: ' + r.json_path + '\nMP4: ' + r.video_path);
}

async function add() {
  const txt = document.getElementById('txt');
  if (!txt.value.trim()) return;
  await api('/annotate', {method:'POST', body: JSON.stringify({text: txt.value})});
  txt.value='';
  refresh();
}

document.getElementById('btnStart').addEventListener('click', start);
document.getElementById('btnStop').addEventListener('click', stopAll);
document.getElementById('btnAdd').addEventListener('click', add);
document.getElementById('txt').addEventListener('keydown', e => { if (e.key === 'Enter') add(); });

setInterval(refresh, 1000);
refresh();
