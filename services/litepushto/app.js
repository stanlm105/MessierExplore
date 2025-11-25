import { astro } from './lib/astro.js';
import { sensors } from './lib/sensors.js';
import { aligner } from './lib/aligner.js';
import { targets } from './lib/targets.js';

const $ = s => document.querySelector(s);
const diag = msg => { const p = $('#diag'); p.textContent = msg; };
const show = (el, on=true)=> el.classList && (el.classList.toggle('hidden', !on));

// Console logger for on-screen debugging
const logs = [];
function log(msg){
  const now = new Date().toISOString().substr(11,8);
  logs.push(`[${now}] ${msg}`);
  if(logs.length > 20) logs.shift();
  const c = $('#console');
  if(c) c.textContent = logs.join('\n');
  console.log(msg);
}

let state = {
  loc: null,              // {lat, lon}
  qDevice: null,          // quaternion from sensors
  qAlign: null,           // alignment correction quaternion
  qNudge: [1,0,0,0],      // micro-adjust quaternion
  now: () => new Date(),
  selectedTarget: null,
  alignSamples: [],       // collected alignment samples [{qDevice, targetVec, label, weight?}]
  flipScreen: false       // if true, invert device forward axis
};

// Cache target Alt/Az at 1 Hz to follow Earth's rotation without overusing CPU
let targetCache = { id: null, alt: null, az: null, ts: 0 };
function getTargetAltAz(nowMs){
  if(!state.selectedTarget || !state.loc) return null;
  const id = state.selectedTarget.number || state.selectedTarget.name || 'custom';
  if(id !== targetCache.id || (nowMs - targetCache.ts) >= 1000 || targetCache.alt === null){
    const t = targets.altazForTarget(state.selectedTarget, state.loc, new Date());
    targetCache = { id, alt: t.alt, az: t.az, ts: nowMs };
  }
  return { alt: targetCache.alt, az: targetCache.az };
}

function clampAngle(a){
  while(a>180) a-=360; while(a<-180) a+=360; return a;
}

async function populateTargets(){
  const list = await targets.loadMessier();
  const sel = $('#messier');
  
  // Add default prompt option
  const prompt = document.createElement('option');
  prompt.value = '';
  prompt.textContent = 'Select target';
  sel.appendChild(prompt);
  
  for(const m of list){
    const opt = document.createElement('option');
    opt.value = m.number;
    opt.textContent = `M${m.number} — ${m.name || m.ngc || ''}`;
    sel.appendChild(opt);
  }
}

async function ensurePermissions(){
  log('ensurePermissions() called');
  try{
    log('Checking API availability...');
    log(`DeviceMotionEvent: ${typeof window.DeviceMotionEvent}`);
    log(`DeviceOrientationEvent: ${typeof window.DeviceOrientationEvent}`);
    log(`navigator.geolocation: ${typeof navigator.geolocation}`);
    
    if(window.DeviceMotionEvent && typeof window.DeviceMotionEvent.requestPermission === 'function'){
      log('iOS 13+ DeviceMotionEvent.requestPermission exists');
    }
    if(window.DeviceOrientationEvent && typeof window.DeviceOrientationEvent.requestPermission === 'function'){
      log('iOS 13+ DeviceOrientationEvent.requestPermission exists');
    }
    
    log('Requesting permissions...');
    const perm = await sensors.requestPermissions();
    log(`Permission result: motion=${perm.motion}, orientation=${perm.orientation}`);
    
    log('Starting sensor listeners...');
    const started = await sensors.start(4000);
    log(`Sensor start result: ${started}`);
    
    log('Requesting geolocation...');
    state.loc = await sensors.getLocation();
    log(`Location: ${state.loc.lat.toFixed(4)}, ${state.loc.lon.toFixed(4)}`);
    
    $('#enableSensors').disabled = true;
    diag(`Permissions: motion=${perm.motion}, orientation=${perm.orientation}\nSensors started: ${started?'yes':'no'}\nLocation: ${state.loc.lat.toFixed(4)}, ${state.loc.lon.toFixed(4)}`);
    
    if(!(perm.motion==='granted' || perm.motion==='implicit' || perm.motion==='not_supported') ||
       !(perm.orientation==='granted' || perm.orientation==='implicit' || perm.orientation==='not_supported') ||
       !started){
      $('#permBannerText').textContent = 'Could not start sensors. Please tap Try again and allow Motion & Orientation. On iOS, ensure Safari has Motion & Orientation access enabled in Settings > Safari > Privacy & Security.';
      show($('#permBanner'), true);
    } else {
      show($('#permBanner'), false);
    }
  }catch(e){
    log('ERROR: '+e.message);
    log('Stack: '+e.stack);
    diag('Permission error: '+e.message);
    $('#permBannerText').textContent = 'Permission error: '+e.message;
    show($('#permBanner'), true);
  }
}

function computePointing(){
  if(!state.qDevice || !state.qAlign) return null;
  // Apply alignment transform to device orientation
  const qAligned = aligner.applyAlignment(state.qAlign, state.qDevice);
  // Apply micro-nudge on top of alignment
  const qPoint = aligner.applyAlignment(state.qNudge, qAligned);
  // Determine forward axis depending on flipScreen
  const fwd = state.flipScreen ? [0,0,1] : [0,0,-1];
  const v = astro.applyQuatToVector(qPoint, fwd);
  return astro.vectorToAltAz(v);
}

function updateGuidance(){
  const pointing = computePointing();
  if(!pointing || !state.loc || !state.selectedTarget) return;

  const nowMs = performance.now();
  const t = getTargetAltAz(nowMs);
  if(!t) return;
  const dAz = clampAngle(t.az - pointing.az);
  const dAlt = clampAngle(t.alt - pointing.alt);
  const dist = Math.hypot(dAz, dAlt);

  $('#deltaAz').textContent = dAz.toFixed(1)+'°';
  $('#deltaAlt').textContent = dAlt.toFixed(1)+'°';
  $('#distance').textContent = dist.toFixed(1)+'°';
  
  drawCompass(dAz, dAlt, dist);
}

function drawCompass(dAz, dAlt, dist){
  const canvas = $('#compass');
  if(!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext('2d');
  const cx = 100, cy = 100, r = 80;
  
  // Clear
  ctx.clearRect(0, 0, 200, 200);
  
  // Circle background
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, 2*Math.PI);
  ctx.strokeStyle = '#24306a';
  ctx.lineWidth = 3;
  ctx.stroke();
  
  // If aligned (distance < 1°), show green thumbs up
  if(dist < 1.0){
    $('#compassStatus').textContent = '👍';
    $('#compassStatus').style.color = '#4ade80';
    ctx.fillStyle = '#4ade80';
    ctx.font = 'bold 48px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('✓', cx, cy);
    return;
  }
  
  // Color based on distance: red (far) -> orange -> yellow -> white (close)
  let color;
  if(dist > 10) color = '#ef4444'; // red
  else if(dist > 5) color = '#f97316'; // orange
  else if(dist > 2) color = '#fbbf24'; // yellow
  else color = '#e5e7eb'; // white
  
  $('#compassStatus').textContent = dist.toFixed(1)+'° off';
  $('#compassStatus').style.color = color;
  
  // Arrow pointing direction: dAz=right/left (x), dAlt=up/down (y)
  // Angle from center: atan2(dAlt, dAz) but rotated so up is -90deg
  const angle = Math.atan2(-dAlt, dAz); // negative dAlt because canvas Y is inverted
  const arrowLen = Math.min(r*0.6, dist*8); // scale arrow length with distance
  const tipX = cx + Math.cos(angle)*arrowLen;
  const tipY = cy + Math.sin(angle)*arrowLen;
  
  // Draw arrow
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(tipX, tipY);
  ctx.stroke();
  
  // Arrowhead
  const headLen = 15;
  const headAngle = Math.PI/6;
  ctx.beginPath();
  ctx.moveTo(tipX, tipY);
  ctx.lineTo(tipX - headLen*Math.cos(angle-headAngle), tipY - headLen*Math.sin(angle-headAngle));
  ctx.lineTo(tipX - headLen*Math.cos(angle+headAngle), tipY - headLen*Math.sin(angle+headAngle));
  ctx.closePath();
  ctx.fill();
}

function schedule(){
  updateGuidance();
  setTimeout(schedule, 100); // Update every 100ms (10fps) - responsive but readable
}

function bindUI(){
  log('Binding UI...');
  $('#enableSensors').addEventListener('click', ()=>{
    log('Enable Sensors button clicked');
    ensurePermissions();
  });
  $('#bannerTryAgain').addEventListener('click', ()=>{
    log('Try Again button clicked');
    ensurePermissions();
  });
  $('#bannerHelp').addEventListener('click', ()=>{
    const help = 'To enable sensors on iPhone:\n\n1) Use HTTPS.\n2) Tap Enable Sensors, then Allow Motion & Orientation.\n3) If no prompt appears, open Settings > Safari > Advanced > Experimental Features and ensure Motion features are enabled; also check Settings > Safari > Privacy & Security > Motion & Orientation Access is ON.';
    alert(help);
  });
  $('#alignBtn').addEventListener('click', async () => {
    try{
      if(!state.loc) state.loc = await sensors.getLocation();
      const starId = $('#alignStar').value;
      const starAltAz = targets.altazForStar(starId, state.loc, state.now());
            // Two-star alignment collection
            const targetVec = astro.altazToVector(starAltAz.alt, starAltAz.az);
            state.alignSamples.push({ qDevice: state.qDevice, targetVec, label: starId });
            if(state.alignSamples.length === 1){
          // First star captured
          $('#alignStatus').textContent = `1/2 captured: ${starId.toUpperCase()} (Alt ${starAltAz.alt.toFixed(1)}°, Az ${starAltAz.az.toFixed(1)}°). Point a different bright star and press Align again.`;
          // Provide a provisional one-star alignment so guidance is somewhat helpful
          state.qAlign = aligner.alignToAltAz(state.qDevice, starAltAz);
          state.qNudge = [1,0,0,0];
        } else {
                // Have at least two, compute robust two-star rotation using last two samples
                const samples = state.alignSamples.slice(-2);
                const q = aligner.solveTwoStar(samples);
          if(q){
            state.qAlign = q;
            state.qNudge = [1,0,0,0];
            const s1 = samples[0].label.toUpperCase();
            const s2 = samples[1].label.toUpperCase();
            $('#alignStatus').textContent = `2-star aligned: ${s1} + ${s2}. You can refine by adding another pair.`;
            // Keep only the last two to avoid overweighting older samples
            state.alignSamples = samples;
          } else {
            $('#alignStatus').textContent = 'Two-star solve failed; falling back to single-star.';
            state.qAlign = aligner.alignToAltAz(state.qDevice, starAltAz);
            state.qNudge = [1,0,0,0];
            // Reset samples to just this one
            state.alignSamples = [{ qDevice: state.qDevice, targetVec, label: starId }];
          }
        }
    }catch(e){ diag('Align error: '+e.message); }
  });
  // Also reset micro-adjust when user changes alignment star
  $('#alignStar').addEventListener('change', () => { state.qNudge = [1,0,0,0]; });
  $('#flipScreen').addEventListener('change', (e) => {
    state.flipScreen = e.target.checked;
    // Reset caches to avoid stale deltas
    targetCache.ts = 0;
    const root = document.querySelector('main');
    if(root) root.classList.toggle('rotated', state.flipScreen);
  });
  
  // Refine: use weighted best-fit over last up to 4 samples (weights: 0.5, 0.8, 1.0, 1.0)
  $('#refineHere').addEventListener('click', () => {
    try{
      if(!state.selectedTarget || !state.qDevice || !state.loc){ return; }
      const now = new Date();
      const tAltAz = targets.altazForTarget(state.selectedTarget, state.loc, now);
      const targetVec = astro.altazToVector(tAltAz.alt, tAltAz.az);
      // Add sample for current device orientation → current target
      state.alignSamples.push({ qDevice: state.qDevice, targetVec, label: `ref:${state.selectedTarget.number||state.selectedTarget.name||'t'}` });
      // Keep only last 4 samples for stability
      if(state.alignSamples.length > 4) state.alignSamples = state.alignSamples.slice(-4);
      // Apply recency weights: oldest→newest = [0.5, 0.8, 1.0, 1.0] (trimmed to length)
      const baseWeights = [0.5, 0.8, 1.0, 1.0];
      const start = Math.max(0, baseWeights.length - state.alignSamples.length);
      const weights = baseWeights.slice(start);
      const weighted = state.alignSamples.map((s, i) => ({ ...s, weight: weights[i] ?? 1.0 }));
      // Solve best-fit over recent weighted samples
      const q = aligner.solveBestFit(weighted);
      if(q){
        state.qAlign = q;
        state.qNudge = [1,0,0,0];
        $('#alignStatus').textContent = `Refined alignment using ${state.alignSamples.length} samples`;
      }
    }catch(e){ diag('Refine error: '+e.message); }
  });
  $('#resetAlign').addEventListener('click', () => {
    state.qAlign = null;
    state.qNudge = [1,0,0,0];
    state.alignSamples = [];
    $('#alignStatus').textContent = 'Alignment reset. Select a bright star and press Align to capture 1/2.';
  });
  $('#messier').addEventListener('change', async (e) => {
    const val = e.target.value;
    if(!val) {
      state.selectedTarget = null;
      return;
    }
    const m = await targets.getByNumber(parseInt(val, 10));
    state.selectedTarget = m;
    // Reset target cache to update immediately on next tick
    targetCache.ts = 0; targetCache.id = null; targetCache.alt = null; targetCache.az = null;
  });
  document.querySelectorAll('[data-nudge]')
    .forEach(btn => btn.addEventListener('click', () => {
      const [axis,val] = btn.dataset.nudge.split(':');
      const deg = parseFloat(val);
      state.qNudge = aligner.nudge(state.qNudge, axis, deg);
    }));
}

(async function init(){
  await populateTargets();
  bindUI();
  // Ensure UI rotation matches state
  const root = document.querySelector('main');
  const flip = document.getElementById('flipScreen');
  if(root && flip){ root.classList.toggle('rotated', !!flip.checked); }
  sensors.onQuaternion(q => { state.qDevice = q; });
  
  // Live angles meter: display raw alpha/beta/gamma from sensors
  sensors.onRawAngles((raw) => {
    log(`Sensor event: ${raw.source}, α=${raw.alpha}, β=${raw.beta}, γ=${raw.gamma}`);
    if(raw.alpha !== null && raw.beta !== null && raw.gamma !== null){
      $('#alpha').textContent = raw.alpha.toFixed(1)+'°';
      $('#beta').textContent = raw.beta.toFixed(1)+'°';
      $('#gamma').textContent = raw.gamma.toFixed(1)+'°';
    } else if(raw.source === 'devicemotion'){
      // devicemotion fired but no orientation angles; show that motion is alive
      $('#alpha').textContent = 'motion';
      $('#beta').textContent = 'detected';
      $('#gamma').textContent = `(${raw.source})`;
    }
  });
  
  log('App initialized');
  schedule();
})();
