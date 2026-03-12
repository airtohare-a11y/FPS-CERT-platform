/* ==========================================================
   MECHgg — Single Page Application
   All pages, routing, API calls, and rendering
   ========================================================== */

// ── State ────────────────────────────────────────────────
const State = {
  user:  JSON.parse(localStorage.getItem('mechgg_user') || 'null'),
  token: localStorage.getItem('mechgg_token') || null,
};

function setAuth(user, token) {
  State.user  = user;
  State.token = token;
  localStorage.setItem('mechgg_user',  JSON.stringify(user));
  localStorage.setItem('mechgg_token', token);
}
function clearAuth() {
  State.user = null; State.token = null;
  localStorage.removeItem('mechgg_user');
  localStorage.removeItem('mechgg_token');
}
function isAuthed() { return !!State.token && !!State.user; }

// ── API ──────────────────────────────────────────────────
const API_BASE = '';
async function api(method, path, body, isForm = false) {
  const headers = {};
  if (State.token) headers['Authorization'] = `Bearer ${State.token}`;
  if (!isForm && body) headers['Content-Type'] = 'application/json';
  const opts = { method, headers };
  if (body) opts.body = isForm ? body : JSON.stringify(body);
  const res = await fetch(API_BASE + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.detail?.message || data.detail || data.error || `Error ${res.status}`;
    throw Object.assign(new Error(msg), { code: data.detail?.error, status: res.status });
  }
  return data;
}
const GET    = (path)       => api('GET',    path);
const POST   = (path, body) => api('POST',   path, body);
const PATCH  = (path, body) => api('PATCH',  path, body);
const DEL    = (path)       => api('DELETE', path);

// ── Toast ────────────────────────────────────────────────
function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ── Router ───────────────────────────────────────────────
const routes = {
  '/':            renderLanding,
  '/dashboard':   renderDashboard,
  '/upload':      renderUpload,
  '/analysis':    renderAnalysisResult,
  '/rank':        renderRank,
  '/coaching':    renderCoaching,
  '/coaches':     renderCoaches,
  '/coach':       renderCoachProfile,
  '/bookings':    renderBookings,
  '/messages':    renderMessages,
  '/pricing':     renderPricing,
  '/login':       renderLogin,
  '/register':    renderRegister,
  '/legal':       renderLegal,
};

function navigate(path, replace = false) {
  if (replace) history.replaceState(null, '', '#' + path);
  else         history.pushState(null, '',  '#' + path);
  route();
}

function route() {
  const hash = location.hash.slice(1) || '/';
  const [base, ...parts] = hash.split('/').filter(Boolean);
  const key = '/' + (base || '');
  const handler = routes[key];
  updateNav();
  if (handler) handler(parts);
  else renderNotFound();
  window.scrollTo(0, 0);
}

window.addEventListener('hashchange', route);
window.addEventListener('DOMContentLoaded', route);

function updateNav() {
  const authed = isAuthed();
  const linksEl   = document.getElementById('nav-links');
  const actionsEl = document.getElementById('nav-actions');
  const mobileEl  = document.getElementById('mobile-menu');

  const publicLinks = [
    
    {label:'Coaches',      path:'/coaches'},
    {label:'Pricing',      path:'/pricing'},
  ];
  const authLinks = [
    {label:'Dashboard',  path:'/dashboard'},
    {label:'Upload',     path:'/upload'},
    
    {label:'Coaches',    path:'/coaches'},
    {label:'Rank',       path:'/rank'},
  ];
  const links = authed ? authLinks : publicLinks;
  const cur   = location.hash.slice(1) || '/';

  const linkHtml = links.map(l =>
    `<button class="nav-link ${cur.startsWith(l.path) ? 'active':''}" onclick="navigate('${l.path}')">${l.label}</button>`
  ).join('');

  const actHtml = authed
    ? `<span class="label" style="color:var(--text-2)">${State.user.username}</span>
       <button class="btn btn-ghost btn-sm" onclick="logout()">Logout</button>`
    : `<button class="btn btn-ghost btn-sm" onclick="navigate('/login')">Login</button>
       <button class="btn btn-primary btn-sm" onclick="navigate('/register')">Sign Up</button>`;

  linksEl.innerHTML   = linkHtml;
  actionsEl.innerHTML = actHtml;
  mobileEl.innerHTML  = linkHtml + `<div style="margin-top:0.75rem;display:flex;gap:0.5rem">${actHtml}</div>`;
}

function toggleMenu() {
  document.getElementById('mobile-menu').classList.toggle('open');
}

function logout() {
  clearAuth();
  navigate('/');
  toast('Logged out', 'info');
}

function requireAuth() {
  if (!isAuthed()) { navigate('/login'); return false; }
  return true;
}

function setView(html) {
  document.getElementById('main-view').innerHTML = `<div class="fade-in">${html}</div>`;
}

// ── Helpers ──────────────────────────────────────────────
function fmtDate(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'});
}
function fmtTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleTimeString('en-US', {hour:'2-digit', minute:'2-digit'});
}
function osiColor(v) {
  if (!v) return 'var(--text-3)';
  if (v >= 820) return 'var(--purple)';
  if (v >= 730) return '#6366f1';
  if (v >= 650) return 'var(--blue)';
  if (v >= 550) return '#d97706';
  if (v >= 450) return '#a0a8b8';
  if (v >= 350) return '#b45309';
  return 'var(--text-3)';
}
function rankColor(tier) {
  const colors = {9:'#f0a500',8:'var(--purple)',7:'#6366f1',6:'var(--blue)',5:'#d97706',4:'#a0a8b8',3:'#b45309',2:'#9ca3af',1:'#6b7280',0:'var(--text-3)'};
  return colors[tier] || 'var(--text-3)';
}
function starRating(v) {
  if (!v) return '—';
  return '★'.repeat(Math.round(v)) + '☆'.repeat(5 - Math.round(v));
}
function pctBar(val, color) {
  return `<div class="bar-track"><div class="bar-fill" style="width:${val||0}%;background:${color||'var(--accent)'}"></div></div>`;
}

// ================================================================
// PAGES
// ================================================================

// ── Landing ──────────────────────────────────────────────────────
function renderLanding() {
  if (isAuthed()) { navigate('/dashboard', true); return; }
  setView(`
    <div class="hero" style="min-height:92vh;display:flex;align-items:center;position:relative;overflow:hidden">
      <div class="hero-grid"></div>
      <div class="hero-content fade-in" style="max-width:860px;margin:0 auto;padding:6rem 1.5rem;text-align:center">
        <div class="hero-eyebrow" style="font-size:0.78rem;letter-spacing:0.18em;margin-bottom:1.5rem">
          GAMEPLAY ANALYSIS &nbsp;·&nbsp; SKILL CERTIFICATION &nbsp;·&nbsp; ELITE COACHING
        </div>
        <h1 class="hero-title" style="font-size:clamp(2.8rem,7vw,5.5rem);line-height:1.05;margin-bottom:1.5rem">
          PLAY BETTER.<br><span>RANKED BY DATA.</span>
        </h1>
        <p class="hero-sub" style="font-size:clamp(1rem,2vw,1.25rem);max-width:620px;margin:0 auto 2.5rem;color:var(--text-2);line-height:1.7">
          Upload a gameplay clip and get an instant mechanical breakdown — reaction speed, accuracy, tracking, decision-making, and more. Know exactly what to fix. Find a coach who can help you fix it.
        </p>
        <div style="display:flex;gap:1rem;justify-content:center;flex-wrap:wrap;margin-bottom:3.5rem">
          <button class="btn btn-primary btn-lg" onclick="navigate('/register')" style="font-size:1rem;padding:0.85rem 2.2rem">Analyze My Gameplay Free</button>
          <button class="btn btn-outline btn-lg" onclick="navigate('/coaches')" style="font-size:1rem;padding:0.85rem 2.2rem">Find a Coach</button>
        </div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1.5rem;max-width:640px;margin:0 auto">
          <div><div class="hero-stat-num" style="font-size:2.2rem">48</div><div class="hero-stat-label">Games Supported</div></div>
          <div><div class="hero-stat-num" style="font-size:2.2rem">9</div><div class="hero-stat-label">Rank Tiers</div></div>
          <div><div class="hero-stat-num" style="font-size:2.2rem">10</div><div class="hero-stat-label">Skill Dimensions</div></div>
          <div><div class="hero-stat-num" style="font-size:2.2rem">0</div><div class="hero-stat-label">Clips Stored</div></div>
        </div>
      </div>
    </div>

    <section style="padding:5rem 1.5rem;background:var(--bg-2);border-top:1px solid var(--border)">
      <div style="max-width:960px;margin:0 auto">
        <div style="text-align:center;margin-bottom:3rem">
          <div class="label label-accent" style="margin-bottom:0.75rem;display:block">For Players</div>
          <h2 style="font-family:var(--display);font-size:clamp(1.8rem,4vw,2.8rem);margin-bottom:1rem">Stop Guessing. Start Knowing.</h2>
          <p style="color:var(--text-2);max-width:560px;margin:0 auto;line-height:1.7">Most players grind for years without knowing what actually holds them back. MECHgg tells you in minutes.</p>
        </div>
        <div class="feature-grid">
          <div class="feature-cell">
            <div class="feature-icon">🎬</div>
            <div class="feature-title">Upload From Any Platform</div>
            <div class="feature-desc">PC, Xbox, PlayStation, mobile, or paste a URL. Your clip is analyzed then permanently deleted — we keep the data, never the footage.</div>
          </div>
          <div class="feature-cell">
            <div class="feature-icon">⚙️</div>
            <div class="feature-title">10-Dimension Breakdown</div>
            <div class="feature-desc">Target acquisition, spray control, tracking, crosshair placement, close-quarters, long-range, engagement decisions, consistency and more — across FPS, MOBA, Fighting, Racing, Sports, and Mobile titles.</div>
          </div>
          <div class="feature-cell">
            <div class="feature-icon">🎯</div>
            <div class="feature-title">OSI Score 0-1000</div>
            <div class="feature-desc">Your Objective Skill Index ranks you against every player on the platform. See exactly where you stand — not just in your lobby, but globally.</div>
          </div>
          <div class="feature-cell">
            <div class="feature-icon">🏋️</div>
            <div class="feature-title">Personalised Drill Plans</div>
            <div class="feature-desc">Not generic tips. Drills built from your exact weak points — specific exercises targeting the habits pulling your rank down.</div>
          </div>
          <div class="feature-cell">
            <div class="feature-icon">📊</div>
            <div class="feature-title">Track Your Progress</div>
            <div class="feature-desc">Every session is logged. Watch your metrics move over time and prove to yourself that the work is paying off.</div>
          </div>
          <div class="feature-cell">
            <div class="feature-icon">🏆</div>
            <div class="feature-title">Earn Your Rank</div>
            <div class="feature-desc">Five sessions unlocks your certified OSI rank and in-game role suggestion — based entirely on your mechanics, not your in-game rank.</div>
          </div>
        </div>
      </div>
    </section>

    <section style="padding:5rem 1.5rem;background:var(--bg-1);border-top:1px solid var(--border)">
      <div style="max-width:960px;margin:0 auto">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4rem;align-items:center">
          <div>
            <div class="label label-accent" style="margin-bottom:0.75rem;display:block">For Coaches</div>
            <h2 style="font-family:var(--display);font-size:clamp(1.8rem,4vw,2.5rem);margin-bottom:1.25rem;line-height:1.1">Your Skills.<br>Your Platform.<br>Your Customers.</h2>
            <p style="color:var(--text-2);line-height:1.8;margin-bottom:1.5rem">MECHgg connects verified coaches with players who already know exactly what they need help with. No cold pitching. Players come to you with data.</p>
            <ul style="list-style:none;padding:0;display:flex;flex-direction:column;gap:0.75rem;margin-bottom:2rem">
              <li style="font-size:0.9rem;color:var(--text-2)">⭐ Build reputation through verified player upvotes</li>
              <li style="font-size:0.9rem;color:var(--text-2)">📋 Profile shows your specialties and supported games</li>
              <li style="font-size:0.9rem;color:var(--text-2)">💬 Players message you directly through the platform</li>
              <li style="font-size:0.9rem;color:var(--text-2)">🔒 You set your own rates — payments are between you and the player</li>
              <li style="font-size:0.9rem;color:var(--text-2)">📈 More upvotes = higher placement in coach listings</li>
            </ul>
            <button class="btn btn-primary" onclick="navigate('/register')">Apply to Coach</button>
          </div>
          <div style="display:flex;flex-direction:column;gap:1rem">
            <div class="panel" style="border-left:3px solid var(--accent)">
              <div style="font-family:var(--display);font-size:1.1rem;margin-bottom:0.4rem">Players arrive with data</div>
              <p style="font-size:0.85rem;color:var(--text-2);line-height:1.6">Every player has an OSI score and full mechanical breakdown before they contact you. No wasted discovery sessions.</p>
            </div>
            <div class="panel" style="border-left:3px solid var(--accent)">
              <div style="font-family:var(--display);font-size:1.1rem;margin-bottom:0.4rem">Reputation that compounds</div>
              <p style="font-size:0.85rem;color:var(--text-2);line-height:1.6">Pro members upvote coaches after sessions. The more you deliver, the higher you rank in listings.</p>
            </div>
            <div class="panel" style="border-left:3px solid var(--accent)">
              <div style="font-family:var(--display);font-size:1.1rem;margin-bottom:0.4rem">Full control of your business</div>
              <p style="font-size:0.85rem;color:var(--text-2);line-height:1.6">Set your own rates, schedule, and game specialties. MECHgg is your storefront — not your employer.</p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section style="padding:2rem 1.5rem;background:var(--bg-3);border-top:1px solid var(--border)">
      <div style="max-width:760px;margin:0 auto;text-align:center">
        <p style="font-size:0.75rem;color:var(--text-3);line-height:1.7">MECHgg is a gameplay analysis and community platform. We do not employ, endorse, or guarantee any coach listed on this platform. All coaching arrangements, payments, and agreements are made directly between coaches and players. MECHgg accepts no liability for the quality, outcomes, or conduct of any coaching relationship. By using this platform you agree to our Terms of Service.</p>
      </div>
    </section>

    <section style="padding:5rem 1.5rem;background:var(--bg-2);border-top:1px solid var(--border)">
      <div style="max-width:860px;margin:0 auto;text-align:center">
        <div class="label label-accent" style="margin-bottom:0.75rem;display:block">Simple Pricing</div>
        <h2 style="font-family:var(--display);font-size:clamp(1.8rem,4vw,2.5rem);margin-bottom:3rem">Start Free. Upgrade When Ready.</h2>
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:1.5rem;text-align:left">
          <div class="panel" style="border:2px solid var(--border);padding:2rem">
            <div style="font-family:var(--display);font-size:1.3rem;margin-bottom:0.25rem">Free</div>
            <div style="font-size:2.5rem;font-weight:800;color:var(--border);line-height:1">$0</div>
            <div style="font-size:0.75rem;color:var(--text-3);margin-bottom:1.5rem">forever</div>
            <ul style="list-style:none;padding:0;display:flex;flex-direction:column;gap:0.6rem;margin-bottom:1.75rem">
              <li style="font-size:0.82rem;color:var(--text-2)">✓ 3 analyses per month</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ OSI score + breakdown</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Habit detection</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Browse coaches</li>
            </ul>
            <button class="btn btn-outline btn-full" onclick="navigate('/register')">Start Free</button>
          </div>
          <div class="panel" style="border:2px solid var(--accent);padding:2rem;position:relative">
            <div style="position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:var(--accent);color:#000;font-size:0.7rem;font-weight:800;padding:0.25rem 0.75rem;border-radius:99px;white-space:nowrap;letter-spacing:0.1em">MOST POPULAR</div>
            <div style="font-family:var(--display);font-size:1.3rem;margin-bottom:0.25rem">Pro</div>
            <div style="font-size:2.5rem;font-weight:800;color:var(--accent);line-height:1">$7</div>
            <div style="font-size:0.75rem;color:var(--text-3);margin-bottom:1.5rem">per month</div>
            <ul style="list-style:none;padding:0;display:flex;flex-direction:column;gap:0.6rem;margin-bottom:1.75rem">
              <li style="font-size:0.82rem;color:var(--text-2)">✓ 30 analyses per month</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Full drill plans</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Role certification</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Upvote coaches</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Priority support</li>
            </ul>
            <button class="btn btn-primary btn-full" onclick="navigate('/register')">Go Pro</button>
          </div>
          <div class="panel" style="border:2px solid var(--border);padding:2rem">
            <div style="font-family:var(--display);font-size:1.3rem;margin-bottom:0.25rem">Coach</div>
            <div style="font-size:2.5rem;font-weight:800;color:var(--text-3);line-height:1">$29</div>
            <div style="font-size:0.75rem;color:var(--text-3);margin-bottom:1.5rem">per month</div>
            <ul style="list-style:none;padding:0;display:flex;flex-direction:column;gap:0.6rem;margin-bottom:1.75rem">
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Unlimited analyses</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Coach profile listing</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Player upvote system</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Direct messaging</li>
              <li style="font-size:0.82rem;color:var(--text-2)">✓ Admin-verified badge</li>
            </ul>
            <button class="btn btn-outline btn-full" onclick="navigate('/register')">Apply to Coach</button>
          </div>
        </div>
      </div>
    </section>

    <section style="padding:6rem 1.5rem;text-align:center;background:var(--bg-1);border-top:1px solid var(--border)">
      <div style="max-width:600px;margin:0 auto">
        <h2 style="font-family:var(--display);font-size:clamp(2rem,5vw,3.5rem);margin-bottom:1rem;line-height:1.1">Your Rank Is a Data Problem.<br><span style="color:var(--accent)">We Have the Data.</span></h2>
        <p style="color:var(--text-2);margin-bottom:2.5rem;font-size:1.05rem;line-height:1.7">Three free analyses a month. No credit card. Upload your first clip and find out exactly where you stand.</p>
        <button class="btn btn-primary btn-lg" onclick="navigate('/register')" style="font-size:1.05rem;padding:1rem 2.5rem">Get My Free Analysis</button>
      </div>
    </section>
  `);
}

// ── Auth ─────────────────────────────────────────────────────────
function renderLogin() {
  if (isAuthed()) { navigate('/dashboard', true); return; }
  setView(`
    <div class="page" style="max-width:440px;">
      <div class="page-header">
        <div class="page-eyebrow">Authentication</div>
        <div class="page-title">Login</div>
      </div>
      <div class="panel">
        <div class="stack" id="login-form">
          <div class="form-group">
            <label class="label">Username or Email</label>
            <input class="input" id="l-user" type="text" placeholder="your_username" autocomplete="username">
          </div>
          <div class="form-group">
            <label class="label">Password</label>
            <input class="input" id="l-pass" type="password" placeholder="••••••••" autocomplete="current-password">
          </div>
          <div id="login-err" class="alert alert-error" style="display:none"></div>
          <button class="btn btn-primary btn-full" onclick="doLogin()">Login</button>
          <p style="text-align:center;font-size:0.875rem;color:var(--text-2)">
            No account? <button class="footer-link" onclick="navigate('/register')">Sign Up Free</button>
          </p>
        </div>
      </div>
    </div>
  `);
  document.getElementById('l-pass').addEventListener('keydown', e => { if(e.key==='Enter') doLogin(); });
}

async function doLogin() {
  const username = document.getElementById('l-user').value.trim();
  const password = document.getElementById('l-pass').value;
  const errEl    = document.getElementById('login-err');
  errEl.style.display = 'none';
  if (!username || !password) { errEl.textContent = 'Fill in all fields.'; errEl.style.display = 'block'; return; }
  try {
    const data = await POST('/auth/login', { username, password });
    setAuth({ id: data.user_id, username: data.username, tier: data.tier }, data.access_token);
    toast(`Welcome back, ${data.username}!`, 'success');
    navigate('/dashboard');
  } catch(e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
  }
}

function renderRegister() {
  if (isAuthed()) { navigate('/dashboard', true); return; }
  setView(`
    <div class="page" style="max-width:440px;">
      <div class="page-header">
        <div class="page-eyebrow">Create Account</div>
        <div class="page-title">Sign Up Free</div>
      </div>
      <div class="panel">
        <div class="stack">
          <div class="form-group">
            <label class="label">Username</label>
            <input class="input" id="r-user" type="text" placeholder="your_gamertag" autocomplete="username">
          </div>
          <div class="form-group">
            <label class="label">Email</label>
            <input class="input" id="r-email" type="email" placeholder="you@example.com" autocomplete="email">
          </div>
          <div class="form-group">
            <label class="label">Password</label>
            <input class="input" id="r-pass" type="password" placeholder="Min 8 characters" autocomplete="new-password">
          </div>
          <label style="display:flex;align-items:flex-start;gap:0.75rem;cursor:pointer;font-size:0.85rem;color:var(--text-2)">
            <input type="checkbox" id="r-tos" style="margin-top:3px;accent-color:var(--accent)">
            I agree to the <button class="footer-link" style="display:inline" onclick="navigate('/legal')">Terms of Service</button>
          </label>
          <div id="reg-err" class="alert alert-error" style="display:none"></div>
          <button class="btn btn-primary btn-full" onclick="doRegister()">Create Account</button>
          <p style="text-align:center;font-size:0.875rem;color:var(--text-2)">
            Have an account? <button class="footer-link" onclick="navigate('/login')">Login</button>
          </p>
        </div>
      </div>
      <p style="font-size:0.75rem;color:var(--text-3);margin-top:1rem;text-align:center">
        1 free analysis included. No credit card required.
      </p>
    </div>
  `);
}

async function doRegister() {
  const username     = document.getElementById('r-user').value.trim();
  const email        = document.getElementById('r-email').value.trim();
  const password     = document.getElementById('r-pass').value;
  const tos_accepted = document.getElementById('r-tos').checked;
  const errEl        = document.getElementById('reg-err');
  errEl.style.display = 'none';
  if (!username || !email || !password) { errEl.textContent = 'Fill in all fields.'; errEl.style.display='block'; return; }
  if (!tos_accepted) { errEl.textContent = 'You must accept the Terms of Service.'; errEl.style.display='block'; return; }
  try {
    const data = await POST('/auth/register', { username, email, password, tos_accepted });
    setAuth({ id: data.user_id, username: data.username, tier: data.tier }, data.access_token);
    toast(`Welcome to MECHgg, ${data.username}!`, 'success');
    navigate('/upload');
  } catch(e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
  }
}

// ── Dashboard ────────────────────────────────────────────────────
async function renderDashboard() {
  if (!requireAuth()) return;
  setView(`<div class="page-wide"><div style="display:flex;align-items:center;gap:1rem;margin-bottom:2rem"><div class="spinner"></div><span class="label">Loading dashboard...</span></div></div>`);
  try {
    const [stats, rank] = await Promise.all([
      GET('/analysis/stats/dashboard'),
      GET('/coaching/rank/me'),
    ]);
    const unread = await GET('/coaching/messages/unread').catch(() => ({unread:0}));

    const rankColor_ = rankColor(rank.tier || 0);
    setView(`
      <div class="page-wide fade-in">
        <div class="flex-between" style="margin-bottom:2rem;flex-wrap:wrap;gap:1rem">
          <div>
            <div class="page-eyebrow">Welcome back</div>
            <div class="page-title">${State.user.username}</div>
          </div>
          <div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap">
            ${unread.unread > 0 ? `<button class="btn btn-outline btn-sm" onclick="navigate('/bookings')">💬 ${unread.unread} unread</button>` : ''}
            <button class="btn btn-primary" onclick="navigate('/upload')">⌖ Upload Clip</button>
          </div>
        </div>

        <!-- Rank banner -->
        <div class="panel-accent" style="margin-bottom:1.5rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:1.5rem">
          <div>
            <div class="label" style="margin-bottom:0.4rem">Current Rank</div>
            <div style="font-family:var(--display);font-size:2rem;font-weight:600;color:${rankColor_}">${rank.rank || 'Unranked'}</div>
            <div style="font-size:0.82rem;color:var(--text-2);margin-top:0.25rem">${rank.message}</div>
            ${!rank.is_ranked ? `<div style="margin-top:0.75rem">${pctBar(rank.progress||0, 'var(--accent)')}<div class="label" style="margin-top:0.3rem">${rank.total_sessions||0} / 5 sessions</div></div>` : ''}
          </div>
          ${rank.suggested_role ? `
          <div style="text-align:right">
            <div class="label" style="margin-bottom:0.3rem">Suggested Role</div>
            <div style="font-family:var(--display);font-size:1.4rem;color:var(--text-1)">${rank.suggested_role.name}</div>
            <div style="font-size:0.78rem;color:var(--text-2);max-width:200px">${rank.suggested_role.playstyle}</div>
          </div>` : ''}
        </div>

        <!-- Stats -->
        <div class="stat-grid" style="margin-bottom:1.5rem">
          <div class="stat-box">
            <div class="stat-value">${stats.total_analyses || 0}</div>
            <div class="stat-label">Clips Analyzed</div>
          </div>
          <div class="stat-box">
            <div class="stat-value" style="color:${osiColor(stats.best_osi)}">${stats.best_osi ? Math.round(stats.best_osi) : '—'}</div>
            <div class="stat-label">Best OSI</div>
          </div>
          <div class="stat-box">
            <div class="stat-value">${stats.avg_osi ? Math.round(stats.avg_osi) : '—'}</div>
            <div class="stat-label">Avg OSI</div>
          </div>
          <div class="stat-box">
            <div class="stat-value">${stats.percentile ? stats.percentile + '%' : '—'}</div>
            <div class="stat-label">Percentile</div>
          </div>
        </div>

        <div class="grid-2">
          <!-- Recent sessions -->
          <div class="panel">
            <div class="panel-header">
              <span class="label label-accent">Recent Sessions</span>
              <button class="btn btn-ghost btn-sm" onclick="navigate('/upload')">+ New</button>
            </div>
            ${stats.recent_trend && stats.recent_trend.length ? `
              <div class="stack-sm">
                ${stats.recent_trend.map(s => `
                  <div class="flex-between" style="padding:0.5rem 0;border-bottom:1px solid var(--border)">
                    <div>
                      <span style="font-size:0.875rem">${(s.game||'').replace(/_/g,' ').toUpperCase()}</span>
                      <div class="label">${fmtDate(s.at)}</div>
                    </div>
                    <div style="font-family:var(--display);font-size:1.3rem;color:${osiColor(s.osi)}">${s.osi ? Math.round(s.osi) : '—'}</div>
                  </div>
                `).join('')}
              </div>
            ` : `<p style="color:var(--text-3);font-size:0.875rem">No sessions yet. <button class="footer-link" onclick="navigate('/upload')">Upload your first clip.</button></p>`}
          </div>

          <!-- Quick links -->
          <div class="stack">
            <div class="panel card-clickable" onclick="navigate('/rank')">
              <div class="flex-between">
                <div>
                  <div class="label label-accent">Rank & Role</div>
                  <div style="font-size:0.875rem;margin-top:0.3rem;color:var(--text-2)">Full breakdown, progression, and role suggestion</div>
                </div>
                <span style="color:var(--text-3)">→</span>
              </div>
            </div>
            <div class="panel card-clickable" onclick="navigate('/coaching')">
              <div class="flex-between">
                <div>
                  <div class="label label-accent">Coaching Plan</div>
                  <div style="font-size:0.875rem;margin-top:0.3rem;color:var(--text-2)">Drill plan based on your weak metrics</div>
                </div>
                <span style="color:var(--text-3)">→</span>
              </div>
            </div>
            
    `);
  } catch(e) {
    setView(`<div class="page"><div class="alert alert-error">${e.message}</div></div>`);
  }
}

// ── Upload ───────────────────────────────────────────────────────
async function renderUpload() {
  if (!requireAuth()) return;

  let games = [];
  try { games = (await GET('/games/')).games || []; } catch(e) {}

  const STAGES = [
    'Uploading clip...',
    'Reading video metadata...',
    'Detecting engagement windows...',
    'Measuring mechanical patterns...',
    'Scoring dimensions...',
    'Generating habit report...',
    'Finalizing analysis...',
  ];

  const RANKED_CATS = new Set(['fps','racing','sports','strategy','fighting']);
  const grouped = {};
  games.filter(g => RANKED_CATS.has(g.category)).forEach(g => {
    const c = g.category || 'fps';
    if (!grouped[c]) grouped[c] = [];
    grouped[c].push(g);
  });

  const catLabels = {fps:'🎯 FPS / Shooter', racing:'🏎️ Racing', sports:'⚽ Sports', strategy:'⚔️ Strategy', fighting:'👊 Fighting'};

  setView(`
    <div class="page" style="max-width:740px">
      <div class="page-header">
        <div class="page-eyebrow">Analysis Engine</div>
        <div class="page-title">Upload Session</div>
        <div class="page-sub">Upload from any platform. Your clip is analyzed then permanently deleted — never stored.</div>
      </div>

      <!-- Game select -->
      <div class="form-group" style="margin-bottom:1.5rem">
        <label class="label">Game</label>
        <select class="select" id="u-game" onchange="updatePlatformGuide()">
          <option value="">Select your game...</option>
          ${Object.entries(grouped).map(([cat, gs]) =>
            `<optgroup label="${catLabels[cat]||cat}">
              ${gs.map(g => `<option value="${g.id}">${g.emoji} ${g.name}</option>`).join('')}
            </optgroup>`
          ).join('')}
        </select>
      </div>

      <!-- Platform selector -->
      <div class="form-group" style="margin-bottom:1.5rem">
        <label class="label">How are you uploading?</label>
        <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:0.5rem" id="platform-btns">
          ${[
            {id:'pc',    icon:'🖥️',  label:'PC'},
            {id:'xbox',  icon:'🟢',  label:'Xbox'},
            {id:'ps5',   icon:'🔵',  label:'PlayStation'},
            {id:'mobile',icon:'📱',  label:'Mobile'},
            {id:'url',   icon:'🔗',  label:'Paste URL'},
          ].map(p => `
            <button type="button" class="btn btn-ghost platform-btn" id="plat-${p.id}"
              onclick="selectPlatform('${p.id}')"
              style="flex-direction:column;gap:0.25rem;padding:0.75rem 0.25rem;font-size:0.75rem">
              <span style="font-size:1.3rem">${p.icon}</span>${p.label}
            </button>
          `).join('')}
        </div>
      </div>

      <!-- Platform guides (hidden until platform selected) -->
      <div id="platform-guide" style="display:none;margin-bottom:1.5rem"></div>

      <!-- URL input (shown when URL selected) -->
      <div id="url-input-section" style="display:none;margin-bottom:1.5rem">
        <div class="form-group">
          <label class="label">Clip URL</label>
          <input class="input" id="u-url" type="url" placeholder="YouTube, Twitter/X, Discord CDN, or direct .mp4 link..."
            oninput="handleUrlInput(this.value)" style="font-family:var(--mono);font-size:0.85rem">
          <div style="margin-top:0.5rem;font-size:0.75rem;color:var(--text-3)">
            Supported: YouTube · Twitter/X · Discord CDN · Direct MP4/MOV links
          </div>
        </div>
        <div id="u-url-status" style="display:none;margin-top:0.5rem"></div>
      </div>

      <!-- File dropzone (hidden when URL selected) -->
      <div id="file-input-section" style="margin-bottom:1.5rem">
        <div class="form-group">
          <label class="label">Gameplay Clip</label>
          <div class="dropzone" id="u-drop" onclick="document.getElementById('u-file').click()"
               ondragover="event.preventDefault();this.classList.add('drag-over')"
               ondragleave="this.classList.remove('drag-over')"
               ondrop="handleDrop(event)">
            <input id="u-file" type="file" accept=".mp4,.mov,.avi,.webm,.mkv" style="display:none" onchange="handleFileSelect(this.files[0])">
            <div id="u-drop-inner">
              <div class="dropzone-icon">📁</div>
              <div style="font-size:0.875rem;color:var(--text-2)">Click to select or drag & drop</div>
              <div class="label" style="margin-top:0.25rem">MP4 · MOV · AVI · WebM · MKV · Max 500MB</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Video preview -->
      <div id="u-preview" style="display:none;border-radius:var(--r-lg);overflow:hidden;background:#000;border:1px solid var(--border);position:relative;margin-bottom:1.5rem">
        <video id="u-video" controls muted loop style="width:100%;max-height:280px;display:block;object-fit:contain"></video>
        <div id="u-overlay" style="display:none;position:absolute;inset:0;background:rgba(5,6,8,0.85);flex-direction:column;align-items:center;justify-content:center;gap:1rem">
          <div id="u-stage" class="label label-accent" style="font-size:0.82rem"></div>
          <div style="width:260px">${pctBar(0,'var(--accent)')}</div>
          <div id="u-pct" class="label" style="font-size:0.72rem">0%</div>
        </div>
      </div>

      <div id="u-err" class="alert alert-error" style="display:none;margin-bottom:1rem"></div>

      <button class="btn btn-primary btn-full btn-lg" id="u-submit" onclick="submitUpload()" disabled>
        ⌖ Analyze Mechanics
      </button>

      <div class="panel" style="background:var(--bg-4);margin-top:1.5rem">
        <div class="label label-accent" style="margin-bottom:0.4rem">📅 Upload in Chronological Order</div>
        <p style="font-size:0.82rem;color:var(--text-2);line-height:1.6">Upload clips oldest-first for accurate improvement tracking. 30–180 seconds of active gameplay works best. Avoid cutscenes and menus.</p>
      </div>

      <!-- Pipeline -->
      <div style="margin-top:1.5rem">
        <div class="label label-accent" style="margin-bottom:0.75rem">Analysis Pipeline</div>
        <div class="pipeline" id="u-pipeline">
          ${STAGES.map((s,i) => `
            <div class="pipeline-step" id="pipe-${i}">
              <div class="pipeline-dot"></div>
              <span>${(i+1).toString().padStart(2,'0')} — ${s}</span>
            </div>
          `).join('')}
        </div>
      </div>
    </div>
  `);
  selectPlatform('pc');
}

let _uploadFile = null;
let _uploadMode  = 'file'; // 'file' or 'url'
let _uploadUrl   = '';

const PLATFORM_GUIDES = {
  pc: {
    title: '🖥️ PC Upload',
    steps: [
      'Record your clip using your game built-in recorder, NVIDIA ShadowPlay, or OBS.',
      'Clips are usually saved to <code>Videos/[Game Name]/</code> or your OBS output folder.',
      'Drag and drop the file below, or click to browse. MP4 recommended.',
    ],
    tip: null,
  },
  xbox: {
    title: '🟢 Xbox Capture',
    steps: [
      '<b>Option A — Xbox App (easiest):</b> On your Xbox, press <kbd>Xbox button → Capture & share → Upload</kbd> to send the clip to Xbox network.',
      'Open the <b>Xbox app on your PC</b>, go to <b>My Library → Captures</b>, download the clip, then upload here.',
      '<b>Option B — Discord:</b> On Xbox, press <kbd>Xbox button → Capture & share → Share → Discord</kbd>. Open Discord on PC, right-click the video → Save, then upload here.',
      '<b>Option C — USB:</b> Plug a USB drive into your Xbox. Go to <b>Settings → Captures → Copy all captures to USB</b>. Plug into PC and upload.',
    ],
    tip: 'Xbox clips are MP4 format — they work perfectly. Set clip length to 30–90 seconds before recording in <b>Settings → Preferences → Capture & share</b>.',
  },
  ps5: {
    title: '🔵 PlayStation Capture',
    steps: [
      '<b>Option A — PS App (easiest):</b> On PS5, create a clip (<kbd>Create button → Save Clip</kbd>). Open the <b>PlayStation App</b> on your phone, go to <b>Game Library → [Game] → Captures</b>, download to phone, then upload here from your phone.',
      '<b>Option B — Discord:</b> On PS5, go to <b>Create → Share → Discord</b>. Open Discord on PC, right-click the video → Save, then upload here.',
      '<b>Option C — USB:</b> Insert a USB drive (FAT32 formatted). Go to <b>Settings → Storage → Console Storage → Captures → Copy to USB</b>. Plug into PC and upload.',
    ],
    tip: 'PS5 saves clips as MP4 via USB. The PlayStation App method is fastest for most users.',
  },
  mobile: {
    title: '📱 Mobile Upload',
    steps: [
      'This page is fully mobile-optimized. Open MECHgg on your phone browser.',
      '<b>Xbox:</b> Share clip to Xbox app on phone → download → upload here.',
      '<b>PS5:</b> Share to PlayStation App → save to phone → upload here.',
      'Tap the upload area below and select the video from your camera roll or files app.',
    ],
    tip: 'On iPhone, clips shared from Xbox/PS apps appear in the Photos app. On Android, check the Downloads folder.',
  },
  url: {
    title: '🔗 Paste a URL',
    steps: [
      '<b>YouTube:</b> Upload your clip as an unlisted video, copy the URL, paste below.',
      '<b>Twitter/X:</b> Post your clip to Twitter, copy the tweet URL, paste below.',
      '<b>Discord:</b> Share your clip to any Discord channel, right-click the video → <b>Copy Link</b>, paste below.',
      '<b>Direct link:</b> Any public .mp4 or .mov URL works — from Google Drive (set to public), Dropbox, or any file host.',
    ],
    tip: 'Max 3 minutes. Discord CDN links are the most reliable. YouTube and Twitter/X links also work.',
  },
};

function selectPlatform(id) {
  _uploadMode = id === 'url' ? 'url' : 'file';

  // Update button styles
  document.querySelectorAll('.platform-btn').forEach(b => {
    b.style.borderColor = '';
    b.style.color = '';
    b.style.background = '';
  });
  const active = document.getElementById('plat-' + id);
  if (active) {
    active.style.borderColor = 'var(--accent)';
    active.style.color = 'var(--accent)';
    active.style.background = 'rgba(245,197,27,0.07)';
  }

  // Show/hide sections
  const urlSection  = document.getElementById('url-input-section');
  const fileSection = document.getElementById('file-input-section');
  if (urlSection)  urlSection.style.display  = id === 'url' ? 'block' : 'none';
  if (fileSection) fileSection.style.display = id === 'url' ? 'none'  : 'block';

  // Show guide
  const guide = PLATFORM_GUIDES[id];
  const guideEl = document.getElementById('platform-guide');
  if (guide && guideEl) {
    guideEl.style.display = 'block';
    guideEl.innerHTML = `
      <div class="panel" style="background:var(--bg-3);border:1px solid var(--border)">
        <div style="font-weight:700;font-size:0.9rem;margin-bottom:0.75rem">${guide.title}</div>
        <ol style="margin:0;padding-left:1.25rem;display:flex;flex-direction:column;gap:0.5rem">
          ${guide.steps.map(s => `<li style="font-size:0.82rem;color:var(--text-2);line-height:1.6">${s}</li>`).join('')}
        </ol>
        ${guide.tip ? `<div style="margin-top:0.75rem;padding:0.6rem 0.75rem;background:rgba(245,197,27,0.07);border-left:2px solid var(--accent);font-size:0.78rem;color:var(--text-2);line-height:1.5">💡 ${guide.tip}</div>` : ''}
      </div>
    `;
  }

  // Re-evaluate submit button
  _checkSubmitReady();
}

function handleUrlInput(val) {
  _uploadUrl = val.trim();
  const statusEl = document.getElementById('u-url-status');
  if (!_uploadUrl) {
    if (statusEl) statusEl.style.display = 'none';
    _checkSubmitReady();
    return;
  }
  const isXbox = /xbox\.com\/play|xboxclips\.com|gameclips\.io/i.test(_uploadUrl);
  const isSupported = /youtube\.com|youtu\.be|twitter\.com|x\.com|twitch\.tv|cdn\.discordapp\.com|media\.discordapp\.net|\.(mp4|mov|avi|webm|mkv)(\?|$)/i.test(_uploadUrl);
  const urlWarnEl = document.getElementById('url-warn');
  if (urlWarnEl) {
    if (isXbox) {
      urlWarnEl.innerHTML = '⚠️ Xbox share links require Microsoft login and cannot be downloaded. <strong>Save the clip to your device</strong> and upload the file directly, or <strong>share to YouTube/Twitter first</strong> then paste that URL.';
      urlWarnEl.style.display = 'block';
    } else if (_uploadUrl && !isSupported) {
      urlWarnEl.innerHTML = '⚠️ Unsupported URL. Supported: YouTube, Twitter/X, Twitch, Discord CDN, or direct .mp4/.mov links.';
      urlWarnEl.style.display = 'block';
    } else {
      urlWarnEl.style.display = 'none';
    }
  }
  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.innerHTML = isSupported
      ? '<span style="color:var(--green);font-size:0.8rem">✅ URL looks good</span>'
      : '<span style="color:var(--text-3);font-size:0.8rem">⚠️ Unrecognized URL — YouTube, Twitter/X, Discord, or direct .mp4 links work best</span>';
  }
  _checkSubmitReady();
}

function _checkSubmitReady() {
  const gameId   = document.getElementById('u-game') ? document.getElementById('u-game').value : '';
  const submitEl = document.getElementById('u-submit');
  if (!submitEl) return;
  if (_uploadMode === 'url') {
    submitEl.disabled = !(_uploadUrl && gameId);
  } else {
    submitEl.disabled = !(_uploadFile && gameId);
  }
}

function updatePlatformGuide() { _checkSubmitReady(); }


function handleDrop(e) {
  e.preventDefault();
  document.getElementById('u-drop').classList.remove('drag-over');
  if (e.dataTransfer.files[0]) handleFileSelect(e.dataTransfer.files[0]);
}
function handleFileSelect(file) {
  if (!file) return;
  _uploadFile = file;
  const dropEl    = document.getElementById('u-drop');
  const previewEl = document.getElementById('u-preview');
  const videoEl   = document.getElementById('u-video');
  const submitEl  = document.getElementById('u-submit');
  dropEl.classList.add('has-file');
  document.getElementById('u-drop-inner').innerHTML = `
    <div class="dropzone-icon">✅</div>
    <div style="font-family:var(--mono);font-size:0.85rem;color:var(--green);font-weight:700">${file.name}</div>
    <div class="label" style="margin-top:0.2rem">${(file.size/1024/1024).toFixed(1)} MB</div>
    <button type="button" onclick="event.stopPropagation();clearFile()" style="margin-top:0.5rem;font-size:0.75rem;color:var(--text-3);background:none;border:none;cursor:pointer">Remove</button>
  `;
  videoEl.src = URL.createObjectURL(file);
  previewEl.style.display = 'block';
  _checkSubmitReady();
}
function clearFile() {
  _uploadFile = null;
  document.getElementById('u-drop').classList.remove('has-file');
  document.getElementById('u-drop-inner').innerHTML = `
    <div class="dropzone-icon">📁</div>
    <div style="font-size:0.875rem;color:var(--text-2)">Click to select or drag & drop</div>
    <div class="label" style="margin-top:0.25rem">MP4 · MOV · AVI · WebM · MKV · Max 500MB</div>
  `;
  document.getElementById('u-preview').style.display = 'none';
  _checkSubmitReady();
}

async function submitUpload() {
  const gameId = document.getElementById('u-game').value;
  const errEl  = document.getElementById('u-err');
  errEl.style.display = 'none';
  if (!gameId) { errEl.textContent='Select a game first.'; errEl.style.display='block'; return; }
  if (_uploadMode === 'url' && !_uploadUrl) { errEl.textContent='Paste a URL first.'; errEl.style.display='block'; return; }
  if (_uploadMode !== 'url' && !_uploadFile) { errEl.textContent='Select a clip first.'; errEl.style.display='block'; return; }

  // Lock UI
  document.getElementById('u-submit').disabled = true;
  document.getElementById('u-submit').innerHTML = '<span class="spinner"></span> Analyzing...';
  const dropEl = document.getElementById('u-drop');
  if (dropEl) dropEl.style.pointerEvents = 'none';

  // Show overlay for file uploads; for URL show a different status
  let stageTimer;
  if (_uploadMode !== 'url') {
    const overlay = document.getElementById('u-overlay');
    overlay.style.display = 'flex';
    document.getElementById('u-video').play().catch(()=>{});
    let stageIdx = 0;
    const stages = document.querySelectorAll('[id^="pipe-"]');
    stages[0].classList.add('active');
    stageTimer = setInterval(() => {
      if (stageIdx < stages.length - 1) {
        stages[stageIdx].classList.remove('active');
        stages[stageIdx].classList.add('complete');
        stageIdx++;
        stages[stageIdx].classList.add('active');
        const pct = Math.round(((stageIdx+1)/stages.length)*95);
        document.getElementById('u-stage').textContent = stages[stageIdx].querySelector('span').textContent;
        document.getElementById('u-pct').textContent   = pct + '%';
        const bar = document.querySelector('#u-overlay .bar-fill');
        if (bar) bar.style.width = pct + '%';
      }
    }, 1800);
  } else {
    // URL mode — animate pipeline without video overlay
    const stages = document.querySelectorAll('[id^="pipe-"]');
    let stageIdx = 0;
    if (stages.length) stages[0].classList.add('active');
    stageTimer = setInterval(() => {
      if (stageIdx < stages.length - 1) {
        stages[stageIdx].classList.remove('active');
        stages[stageIdx].classList.add('complete');
        stageIdx++;
        stages[stageIdx].classList.add('active');
      }
    }, 2200);
  }

  // Get real video duration — progress tied to actual clip length
  const vidEl    = document.getElementById('u-video');
  const duration = (vidEl && vidEl.duration && isFinite(vidEl.duration) && vidEl.duration > 0) ? vidEl.duration : null;
  const bar      = document.querySelector('#u-overlay .bar-fill');
  const pctEl2   = document.getElementById('u-pct');
  const stageEl  = document.getElementById('u-stage');
  const allStages = document.querySelectorAll('[id^="pipe-"]');
  const stageLabels = Array.from(allStages).map(s => s.querySelector('span')?.textContent || '');
  let apiResult  = null;
  let apiError   = null;
  let pct        = 0;
  let rafId      = null;
  let stageIdx2  = 0;
  const startTime = performance.now();
  if (allStages.length) { allStages[0].classList.add('active'); }

  const apiPromise = (_uploadMode === 'url'
    ? api('POST', '/analysis/upload-url', { url: _uploadUrl, game_id: gameId })
    : (() => { const fd = new FormData(); fd.append('clip', _uploadFile); fd.append('game_id', gameId); return api('POST', '/analysis/upload', fd, true); })()
  ).then(r => { apiResult = r; }).catch(e => { apiError = e; });

  function updateProgress() {
    const elapsed = (performance.now() - startTime) / 1000;
    if (apiError) {
      cancelAnimationFrame(rafId);
      const ov = document.getElementById('u-overlay'); if (ov) ov.style.display = 'none';
      document.getElementById('u-submit').disabled  = false;
      document.getElementById('u-submit').innerHTML = '⌖ Analyze Mechanics';
      if (dropEl) dropEl.style.pointerEvents = '';
      allStages.forEach(s => s.classList.remove('active','complete'));
      errEl.textContent = apiError.message || 'Analysis failed. Please try again.';
      errEl.style.display = 'block';
      if (apiError.code === 'QUOTA_EXCEEDED') errEl.innerHTML = `Limit reached. <button class="footer-link" onclick="navigate('/pricing')">Upgrade to Pro</button> for unlimited analyses.`;
      return;
    }
    let targetPct;
    if (duration) {
      targetPct = Math.min((elapsed / duration) * 100, 97);
      if (apiResult && elapsed >= duration) targetPct = 100;
    } else {
      targetPct = apiResult ? 100 : Math.min((elapsed / 60) * 97, 97);
    }
    pct += (targetPct - pct) * 0.05;
    if (bar) bar.style.width = Math.round(pct) + '%';
    if (pctEl2) pctEl2.textContent = Math.round(pct) + '%';
    const newIdx = Math.min(Math.floor((pct / 100) * allStages.length), allStages.length - 1);
    if (newIdx > stageIdx2) {
      allStages[stageIdx2].classList.remove('active'); allStages[stageIdx2].classList.add('complete');
      stageIdx2 = newIdx; allStages[stageIdx2].classList.add('active');
      if (stageEl && stageLabels[stageIdx2]) stageEl.textContent = stageLabels[stageIdx2];
    }
    if (pct >= 99.5 && apiResult) {
      cancelAnimationFrame(rafId);
      allStages.forEach(s => { s.classList.remove('active'); s.classList.add('complete'); });
      if (bar) bar.style.width = '100%';
      if (pctEl2) pctEl2.textContent = '100%';
      if (stageEl) stageEl.textContent = 'Analysis complete';
      setTimeout(() => navigate('/analysis/' + apiResult.id), 500);
      return;
    }
    rafId = requestAnimationFrame(updateProgress);
  }
  rafId = requestAnimationFrame(updateProgress);
  try { await apiPromise; } catch(e) {}
}
}

// ── Analysis Result ──────────────────────────────────────────────
async function renderAnalysisResult(parts) {
  if (!requireAuth()) return;
  const id = parts[0];
  if (!id) { navigate('/dashboard'); return; }
  setView(`<div class="page"><div style="display:flex;gap:1rem;align-items:center"><div class="spinner"></div><span class="label">Loading analysis...</span></div></div>`);
  try {
    const d = await GET(`/analysis/${id}`);
    const scores  = d.metrics || {};
    const habits  = d.habits  || [];
    const dims    = d.dimension_scores || {};

    const metricLabels = d.metric_labels || {
      reaction:'Reaction', accuracy:'Accuracy',
      eng_eff:'Engagement', consistency:'Consistency',
      cqe:'Close Quarters', lre:'Long Range', dpi:'Pressure'
    };
    const gameTitle = d.game_name || (d.game||'').replace(/_/g,' ').toUpperCase();

    setView(`
      <div class="page fade-in">
        <div class="flex-between" style="margin-bottom:2rem;flex-wrap:wrap;gap:1rem">
          <div>
            <div class="page-eyebrow">Analysis Complete</div>
            <div class="page-title">${gameTitle}</div>
            <div class="label" style="margin-top:0.25rem">${fmtDate(d.analyzed_at)}</div>
          </div>
          <button class="btn btn-ghost" onclick="navigate('/upload')">+ New Analysis</button>
        </div>

        <div class="grid-2" style="margin-bottom:1.5rem">
          <!-- OSI Score -->
          <div class="panel-accent" style="text-align:center;padding:2.5rem">
            <div class="label label-accent" style="margin-bottom:0.75rem">OSI Score</div>
            <div class="osi-display">${d.osi ? Math.round(d.osi) : '—'}</div>
            <div style="margin-top:0.75rem;display:flex;gap:1rem;justify-content:center;flex-wrap:wrap">
              ${d.percentile ? `<span class="badge badge-accent">Top ${100-Math.round(d.percentile)}%</span>` : ''}
              ${d.tier ? `<span class="badge badge-ghost">${d.tier}</span>` : ''}
            </div>
          </div>

          <!-- Metric bars -->
          <div class="panel">
            <div class="label label-accent" style="margin-bottom:1rem">Metric Breakdown</div>
            <div class="stack-sm">
              ${Object.entries(metricLabels).map(([k,label]) => {
                const val = scores[k];
                return val != null ? `
                  <div>
                    <div class="flex-between" style="margin-bottom:0.25rem">
                      <span style="font-family:var(--mono);font-size:0.75rem;color:var(--text-2)">${label}</span>
                      <span style="font-family:var(--mono);font-size:0.75rem;color:var(--accent)">${Math.round(val)}</span>
                    </div>
                    ${pctBar(val)}
                  </div>
                ` : '';
              }).join('')}
            </div>
          </div>
        </div>

        <!-- Coaching summary -->
        ${d.coaching_summary ? `
        <div class="panel" style="margin-bottom:1.5rem;border-color:var(--border-hi)">
          <div class="label label-accent" style="margin-bottom:0.5rem">Coaching Summary</div>
          <p style="font-size:0.9rem;color:var(--text-2);line-height:1.7">${d.coaching_summary}</p>
        </div>` : ''}

        <!-- Habits -->
        ${habits.length ? `
        <div style="margin-bottom:1.5rem">
          <div class="label label-accent" style="margin-bottom:0.75rem">Detected Habits (${habits.length})</div>
          <div class="stack-sm">
            ${habits.map(h => `
              <div class="panel" style="border-left:3px solid ${h.isPositive?'var(--green)':'var(--red)'}">
                <div class="flex-between" style="margin-bottom:0.4rem">
                  <span style="font-family:var(--display);font-size:1.1rem">${h.name}</span>
                  <span class="badge ${h.isPositive?'badge-green':'badge-red'}">${h.severity||''}</span>
                </div>
                <p style="font-size:0.85rem;color:var(--text-2);margin-bottom:0.5rem">${h.description}</p>
                ${h.coachingNote ? `<p style="font-size:0.82rem;color:var(--accent);font-family:var(--mono)">💡 ${h.coachingNote}</p>` : ''}
              </div>
            `).join('')}
          </div>
        </div>` : ''}

        <div style="display:flex;gap:1rem;flex-wrap:wrap">
          <button class="btn btn-primary" onclick="navigate('/coaching')">Get Drill Plan</button>
          <button class="btn btn-outline" onclick="navigate('/rank')">View Rank</button>
          <button class="btn btn-ghost" onclick="navigate('/coaches')">Find a Coach</button>
        </div>
      </div>
    `);
  } catch(e) {
    setView(`<div class="page"><div class="alert alert-error">${e.message}</div><button class="btn btn-ghost" style="margin-top:1rem" onclick="navigate('/dashboard')">Back to Dashboard</button></div>`);
  }
}

// ── Rank ─────────────────────────────────────────────────────────
async function renderRank() {
  if (!requireAuth()) return;
  setView(`<div class="page"><div style="display:flex;gap:1rem;align-items:center"><div class="spinner"></div><span class="label">Loading rank data...</span></div></div>`);
  try {
    const [rank, progress] = await Promise.all([
      GET('/coaching/rank/me'),
      GET('/coaching/rank/progress'),
    ]);

    const rc = rankColor(rank.tier || 0);
    const role = rank.suggested_role;

    setView(`
      <div class="page fade-in">
        <div class="page-header">
          <div class="page-eyebrow">Certification</div>
          <div class="page-title">Your Rank</div>
        </div>

        <!-- Rank card -->
        <div class="panel-accent" style="margin-bottom:1.5rem;text-align:center;padding:3rem">
          <div style="font-family:var(--display);font-size:4rem;font-weight:700;color:${rc};text-shadow:0 0 40px ${rc}66;line-height:1">${rank.rank||'Unranked'}</div>
          ${rank.is_ranked ? `
            <div style="display:flex;justify-content:center;gap:2rem;margin-top:1.5rem;flex-wrap:wrap">
              <div><div style="font-family:var(--display);font-size:1.8rem;color:var(--accent)">${rank.avg_osi ? Math.round(rank.avg_osi) : '—'}</div><div class="label">Avg OSI</div></div>
              <div><div style="font-family:var(--display);font-size:1.8rem">${rank.consistency ? Math.round(rank.consistency) : '—'}%</div><div class="label">Consistency</div></div>
              <div><div style="font-family:var(--display);font-size:1.8rem">${rank.total_sessions||0}</div><div class="label">Sessions</div></div>
            </div>
          ` : `
            <div style="margin-top:1.5rem">
              <div style="font-size:0.9rem;color:var(--text-2);margin-bottom:0.75rem">${rank.message}</div>
              <div style="max-width:300px;margin:0 auto">
                ${pctBar(rank.progress||0,'var(--accent)')}
                <div class="label" style="text-align:center;margin-top:0.4rem">${rank.total_sessions||0} / 5 sessions</div>
              </div>
            </div>
          `}
        </div>

        <!-- Rank ladder -->
        <div class="panel" style="margin-bottom:1.5rem">
          <div class="label label-accent" style="margin-bottom:1rem">Rank Ladder</div>
          <div style="display:flex;flex-wrap:wrap;gap:0.5rem">
            ${(progress.ranks||[]).filter(r=>r.tier>0).map(r => `
              <div style="padding:0.4rem 0.9rem;border-radius:var(--r);border:1px solid ${r.tier===(rank.tier||0)?'var(--accent)':'var(--border)'};background:${r.tier===(rank.tier||0)?'var(--accent-dim)':'var(--bg-4)'};font-family:var(--mono);font-size:0.72rem;color:${rankColor(r.tier)};opacity:${r.tier<=(rank.tier||0)?1:0.4}">
                ${r.label}
              </div>
            `).join('')}
          </div>
          ${progress.next_rank ? `
            <div class="alert alert-info" style="margin-top:1rem">
              <span class="label">Next: </span> ${progress.next_rank.label} — need <strong>${progress.next_rank.gap}</strong> more avg OSI
            </div>
          ` : rank.tier===9 ? '<div class="alert alert-success" style="margin-top:1rem">You have reached the highest rank: Apex.</div>' : ''}
        </div>

        <!-- Role suggestion -->
        ${role ? `
        <div class="panel-accent" style="margin-bottom:1.5rem">
          <div class="label label-accent" style="margin-bottom:0.75rem">Suggested Role</div>
          <div style="display:flex;align-items:flex-start;gap:1.5rem;flex-wrap:wrap">
            <div style="flex:1;min-width:200px">
              <div style="font-family:var(--display);font-size:2rem;margin-bottom:0.4rem">${role.name}</div>
              <div class="badge badge-ghost" style="margin-bottom:0.75rem">${role.playstyle}</div>
              <p style="font-size:0.875rem;color:var(--text-2);line-height:1.6">${role.reason}</p>
            </div>
            <div style="min-width:200px">
              <div class="label" style="margin-bottom:0.3rem">Key Drills</div>
              ${role.drills.map(d=>`<div style="font-size:0.82rem;color:var(--text-2);padding:0.15rem 0">▸ ${d}</div>`).join('')}
            </div>
          </div>
        </div>` : `
        <div class="panel" style="margin-bottom:1.5rem">
          <div class="label label-accent" style="margin-bottom:0.4rem">Role Suggestion Locked</div>
          <p style="font-size:0.875rem;color:var(--text-2)">
            Role suggestion unlocks after <strong style="color:var(--text-1)">5 sessions</strong>.
            You have ${rank.total_sessions > 0 ? rank.total_sessions : 0} of 5 so far.
          </p>
        </div>`}

        <!-- Coach eligibility -->
        ${rank.eligible_coach ? `
        <div class="panel" style="border-color:var(--border-hi)">
          <div class="label label-accent" style="margin-bottom:0.4rem">🎓 Coach Eligible</div>
          <p style="font-size:0.875rem;color:var(--text-2);margin-bottom:0.75rem">You are Diamond+ ranked and eligible to apply as a coach.</p>
          <button class="btn btn-outline btn-sm" onclick="navigate('/coaches')">Apply as Coach</button>
        </div>` : ''}

        <div style="margin-top:1.5rem;display:flex;gap:1rem;flex-wrap:wrap">
          <button class="btn btn-primary" onclick="navigate('/upload')">+ Upload Session</button>
          
        </div>
      </div>
    `);
  } catch(e) {
    setView(`<div class="page"><div class="alert alert-error">${e.message}</div></div>`);
  }
}

// ── Coaching Plan ────────────────────────────────────────────────
async function renderCoaching() {
  if (!requireAuth()) return;
  setView(`<div class="page"><div style="display:flex;gap:1rem;align-items:center"><div class="spinner"></div><span class="label">Building your coaching plan...</span></div></div>`);
  try {
    const plan = await GET('/coaching/coaching/plan');
    const metricLabels = {
      reaction:'Reaction Speed', accuracy:'Accuracy', eng_eff:'Engagement Eff.',
      consistency:'Consistency', cqe:'Close Quarters', lre:'Long Range', dpi:'Damage Pressure'
    };
    const statusColors = { improving:'var(--green)', declining:'var(--red)', stable:'var(--text-3)' };
    const statusIcons  = { improving:'↑', declining:'↓', stable:'→' };

    setView(`
      <div class="page fade-in">
        <div class="page-header">
          <div class="page-eyebrow">Personalised Plan</div>
          <div class="page-title">Coaching Plan</div>
          <div class="page-sub">${plan.summary || 'Based on your recent sessions.'}</div>
        </div>

        <!-- Metric trends -->
        ${Object.keys(plan.trends||{}).length ? `
        <div class="panel" style="margin-bottom:1.5rem">
          <div class="label label-accent" style="margin-bottom:1rem">Metric Trends (Last ${plan.session_count} Sessions)</div>
          <div class="stack-sm">
            ${Object.entries(plan.trends).map(([k,t]) => `
              <div>
                <div class="flex-between" style="margin-bottom:0.25rem">
                  <span style="font-family:var(--mono);font-size:0.75rem;color:var(--text-2)">${metricLabels[k]||k}</span>
                  <div style="display:flex;align-items:center;gap:0.5rem">
                    <span style="font-family:var(--mono);font-size:0.68rem;color:${statusColors[t.status]}">${statusIcons[t.status]} ${t.delta>0?'+':''}${t.delta}</span>
                    <span style="font-family:var(--mono);font-size:0.78rem;color:var(--accent)">${t.avg}</span>
                  </div>
                </div>
                ${pctBar(t.avg, statusColors[t.status])}
              </div>
            `).join('')}
          </div>
        </div>` : ''}

        <!-- Drills -->
        ${plan.drills && plan.drills.length ? `
        <div style="margin-bottom:1.5rem">
          <div class="label label-accent" style="margin-bottom:0.75rem">Your Drill Plan</div>
          <div class="stack-sm">
            ${plan.drills.map(d => `
              <div class="panel">
                <div class="flex-between" style="margin-bottom:0.4rem">
                  <span style="font-family:var(--display);font-size:1.15rem">${d.name}</span>
                  <div style="display:flex;gap:0.4rem">
                    <span class="badge badge-ghost">${d.difficulty||''}</span>
                    <span class="badge badge-accent">${d.duration||''}</span>
                  </div>
                </div>
                <p style="font-size:0.85rem;color:var(--text-2);margin-bottom:0.4rem">${d.description}</p>
                <div class="label" style="color:var(--green)">📅 ${d.frequency||''}</div>
              </div>
            `).join('')}
          </div>
        </div>` : `<div class="panel"><p style="color:var(--text-3)">Upload sessions to unlock your personalised drill plan.</p></div>`}

        ${plan.suggested_role ? `
        <div class="panel" style="border-color:var(--border-hi);margin-bottom:1.5rem">
          <div class="label label-accent" style="margin-bottom:0.4rem">Role Match</div>
          <div style="font-family:var(--display);font-size:1.4rem">${plan.suggested_role.name}</div>
          <p style="font-size:0.85rem;color:var(--text-2);margin-top:0.25rem">${plan.suggested_role.description}</p>
        </div>` : ''}

        <div style="display:flex;gap:1rem;flex-wrap:wrap">
          <button class="btn btn-primary" onclick="navigate('/upload')">+ Upload Session</button>
          <button class="btn btn-ghost" onclick="navigate('/coaches')">Find a Coach</button>
        </div>
      </div>
    `);
  } catch(e) {
    setView(`<div class="page"><div class="alert alert-error">${e.message}</div></div>`);
  }
}

// ── Coaches ──────────────────────────────────────────────────────
async function renderCoaches() {
  setView(`<div class="page"><div style="display:flex;gap:1rem;align-items:center"><div class="spinner"></div><span class="label">Loading coaches...</span></div></div>`);
  try {
    const data = await GET('/coaching/coaches');
    const coaches = data.coaches || [];

    setView(`
      <div class="page-wide fade-in">
        <div class="page-header">
          <div class="page-eyebrow">Expert Coaching</div>
          <div class="page-title">Find a Coach</div>
          <div class="page-sub">All coaches are Diamond+ ranked, verified on platform. Browse by specialty or book directly.</div>
        </div>

        ${isAuthed() ? `
        <div class="panel" style="margin-bottom:1.5rem;border-color:var(--border-hi)">
          <div class="flex-between" style="flex-wrap:wrap;gap:1rem">
            <div>
              <div class="label label-accent" style="margin-bottom:0.25rem">Are you Diamond+ ranked?</div>
              <div style="font-size:0.875rem;color:var(--text-2)">Apply to become a coach and earn on platform.</div>
            </div>
            <button class="btn btn-outline btn-sm" onclick="navigate('/coaches/apply')">Apply as Coach</button>
          </div>
        </div>` : ''}

        ${coaches.length ? `
        <div class="coach-grid">
          ${coaches.map(c => `
            <div class="coach-card" onclick="navigate('/coach/${c.id}')">
              <div style="display:flex;gap:1rem;align-items:center;margin-bottom:1rem">
                <div class="coach-avatar">${c.display_name.charAt(0).toUpperCase()}</div>
                <div>
                  <div style="font-family:var(--display);font-size:1.2rem">${c.display_name}</div>
                  <div class="badge badge-accent" style="margin-top:0.2rem">${c.rank}</div>
                </div>
              </div>
              <div style="margin-bottom:0.75rem">
                <span class="badge badge-ghost">${c.role_name||c.specialty_role}</span>
              </div>
              ${c.bio ? `<p style="font-size:0.82rem;color:var(--text-2);margin-bottom:0.75rem;line-height:1.5">${c.bio.slice(0,100)}${c.bio.length>100?'...':''}</p>` : ''}
              <div class="flex-between">
                <div>
                  ${c.avg_rating ? `<div class="stars">${starRating(c.avg_rating)}</div>` : '<div class="label">No reviews yet</div>'}
                  <div class="label">${c.total_sessions} session${c.total_sessions!==1?'s':''}</div>
                </div>
                <div style="text-align:right">
                  <div style="font-family:var(--display);font-size:1.3rem;color:var(--accent)">$${c.rate_per_hour}</div>
                  <div class="label">per hour</div>
                </div>
              </div>
              ${!c.is_available ? `<div class="badge badge-red" style="margin-top:0.75rem;width:100%;justify-content:center">Not Available</div>` : ''}
            </div>
          `).join('')}
        </div>
        ` : `
        <div class="panel" style="text-align:center;padding:3rem">
          <div style="font-size:2rem;margin-bottom:1rem">👥</div>
          <div style="font-family:var(--display);font-size:1.5rem;margin-bottom:0.5rem">No coaches yet</div>
          <p style="color:var(--text-2)">Be the first. Diamond+ ranked players can apply to coach.</p>
        </div>
        `}
      </div>
    `);
  } catch(e) {
    setView(`<div class="page"><div class="alert alert-error">${e.message}</div></div>`);
  }
}

// ── Coach Profile ────────────────────────────────────────────────
async function renderCoachProfile(parts) {
  const coachId = parts[0];
  if (!coachId) { navigate('/coaches'); return; }
  setView(`<div class="page"><div class="spinner"></div></div>`);
  try {
    const c = await GET(`/coaching/coaches/${coachId}`);
    setView(`
      <div class="page fade-in">
        <button class="btn btn-ghost btn-sm" onclick="navigate('/coaches')" style="margin-bottom:1.5rem">← Back to Coaches</button>
        <div class="grid-2" style="gap:2rem">
          <div>
            <div class="panel-accent" style="margin-bottom:1.5rem">
              <div style="display:flex;gap:1rem;align-items:center;margin-bottom:1.25rem">
                <div class="coach-avatar" style="width:64px;height:64px;font-size:1.8rem">${c.display_name.charAt(0)}</div>
                <div>
                  <div style="font-family:var(--display);font-size:1.8rem">${c.display_name}</div>
                  <div style="display:flex;gap:0.5rem;margin-top:0.3rem;flex-wrap:wrap">
                    <span class="badge badge-accent">${c.rank}</span>
                    <span class="badge badge-ghost">${c.role_name||c.specialty_role}</span>
                  </div>
                </div>
              </div>
              ${c.bio ? `<p style="font-size:0.9rem;color:var(--text-2);line-height:1.7;margin-bottom:1rem">${c.bio}</p>` : ''}
              <div class="flex-between">
                <div>
                  <div style="font-family:var(--display);font-size:2rem;color:var(--accent)">$${c.rate_per_hour}</div>
                  <div class="label">per hour</div>
                </div>
                <div style="text-align:right">
                  ${c.avg_rating ? `<div class="stars" style="font-size:1.1rem">${starRating(c.avg_rating)}</div>` : ''}
                  <div class="label">${c.total_sessions} sessions completed</div>
                </div>
              </div>
            </div>

            ${c.role_description ? `
            <div class="panel" style="margin-bottom:1.5rem">
              <div class="label label-accent" style="margin-bottom:0.5rem">Specialty: ${c.role_name}</div>
              <p style="font-size:0.875rem;color:var(--text-2)">${c.role_description}</p>
            </div>` : ''}
          </div>

          <!-- Book -->
          <div>
            ${isAuthed() && c.is_available ? `
            <div class="panel-accent">
              <div class="label label-accent" style="margin-bottom:1rem">Book a Session</div>
              <div class="stack">
                <div class="form-group">
                  <label class="label">Introduce yourself</label>
                  <textarea class="textarea" id="book-msg" placeholder="Tell the coach about your goals, main game, current issues..."></textarea>
                </div>
                <div id="book-err" class="alert alert-error" style="display:none"></div>
                <button class="btn btn-primary btn-full" onclick="bookCoach(${c.id})">Request Session</button>
                <p style="font-size:0.75rem;color:var(--text-3);text-align:center">Coach will accept or decline within 24 hours.</p>
              </div>
            </div>
            ` : !isAuthed() ? `
            <div class="panel">
              <p style="color:var(--text-2);margin-bottom:1rem">Login to book a session with this coach.</p>
              <button class="btn btn-primary" onclick="navigate('/login')">Login</button>
            </div>
            ` : `
            <div class="panel">
              <div class="badge badge-red" style="margin-bottom:0.75rem">Not Currently Available</div>
              <p style="font-size:0.875rem;color:var(--text-2)">This coach is not accepting new bookings right now.</p>
            </div>
            `}
          </div>
        </div>
      </div>
    `);
  } catch(e) {
    setView(`<div class="page"><div class="alert alert-error">${e.message}</div></div>`);
  }
}

async function bookCoach(coachId) {
  if (!requireAuth()) return;
  const msg    = document.getElementById('book-msg').value.trim();
  const errEl  = document.getElementById('book-err');
  errEl.style.display = 'none';
  if (!msg) { errEl.textContent='Write an intro message.'; errEl.style.display='block'; return; }
  try {
    const data = await POST('/coaching/bookings', { coach_id: coachId, message: msg });
    toast('Booking request sent!', 'success');
    navigate('/bookings');
  } catch(e) {
    errEl.textContent = e.message;
    errEl.style.display = 'block';
  }
}

// ── Bookings ─────────────────────────────────────────────────────
async function renderBookings() {
  if (!requireAuth()) return;
  setView(`<div class="page"><div class="spinner"></div></div>`);
  try {
    const data = await GET('/coaching/bookings/me');
    const all  = [...data.as_player.map(b=>({...b,myRole:'player'})), ...data.as_coach.map(b=>({...b,myRole:'coach'}))];
    all.sort((a,b) => b.created_at - a.created_at);

    const statusBadge = s => {
      const m = {pending:'badge-accent',accepted:'badge-green',completed:'badge-blue',declined:'badge-red',cancelled:'badge-ghost'};
      return `<span class="badge ${m[s]||'badge-ghost'}">${s}</span>`;
    };

    setView(`
      <div class="page fade-in">
        <div class="page-header">
          <div class="page-eyebrow">Sessions</div>
          <div class="page-title">My Bookings</div>
        </div>
        ${all.length ? `
        <div class="stack">
          ${all.map(b => `
            <div class="panel card-clickable" onclick="navigate('/messages/${b.id}')">
              <div class="flex-between" style="flex-wrap:wrap;gap:0.75rem">
                <div>
                  ${statusBadge(b.status)}
                  <div class="label" style="margin-top:0.3rem">${b.myRole === 'player' ? 'You as player' : 'You as coach'}</div>
                </div>
                <div style="text-align:right">
                  ${b.rate ? `<div style="font-family:var(--display);font-size:1.2rem;color:var(--accent)">$${b.rate}/hr</div>` : ''}
                  <div class="label">${fmtDate(b.created_at)}</div>
                </div>
              </div>
              ${b.status === 'pending' && b.myRole === 'coach' ? `
              <div style="margin-top:0.75rem;display:flex;gap:0.5rem">
                <button class="btn btn-primary btn-sm" onclick="event.stopPropagation();updateBooking(${b.id},'accept')">Accept</button>
                <button class="btn btn-danger btn-sm" onclick="event.stopPropagation();updateBooking(${b.id},'decline')">Decline</button>
              </div>` : ''}
              ${b.status === 'accepted' && b.myRole === 'coach' ? `
              <div style="margin-top:0.75rem">
                <button class="btn btn-outline btn-sm" onclick="event.stopPropagation();updateBooking(${b.id},'complete')">Mark Complete</button>
              </div>` : ''}
            </div>
          `).join('')}
        </div>
        ` : `
        <div class="panel" style="text-align:center;padding:3rem">
          <div style="font-size:2rem;margin-bottom:1rem">📅</div>
          <div style="font-family:var(--display);font-size:1.5rem;margin-bottom:0.5rem">No bookings yet</div>
          <button class="btn btn-primary" style="margin-top:1rem" onclick="navigate('/coaches')">Find a Coach</button>
        </div>
        `}
      </div>
    `);
  } catch(e) {
    setView(`<div class="page"><div class="alert alert-error">${e.message}</div></div>`);
  }
}

async function updateBooking(id, action) {
  try {
    await PATCH(`/coaching/bookings/${id}`, { action });
    toast(`Booking ${action}ed`, 'success');
    renderBookings();
  } catch(e) { toast(e.message, 'error'); }
}

// ── Messages ─────────────────────────────────────────────────────
async function renderMessages(parts) {
  if (!requireAuth()) return;
  const bookingId = parts[0];
  if (!bookingId) { navigate('/bookings'); return; }
  setView(`<div class="page"><div class="spinner"></div></div>`);
  try {
    const data = await GET(`/coaching/bookings/${bookingId}/messages`);
    const msgs = data.messages || [];

    setView(`
      <div class="page fade-in" style="max-width:680px">
        <div class="flex-between" style="margin-bottom:1.5rem">
          <button class="btn btn-ghost btn-sm" onclick="navigate('/bookings')">← Bookings</button>
          <span class="label">Booking #${bookingId}</span>
        </div>
        <div class="panel" style="margin-bottom:1rem">
          <div class="message-thread" id="msg-thread">
            ${msgs.length ? msgs.map(m => `
              <div>
                <div class="message-bubble ${m.is_mine?'mine':'theirs'}">${m.content}</div>
                <div class="msg-meta" style="text-align:${m.is_mine?'right':'left'}">${fmtDate(m.sent_at)} ${fmtTime(m.sent_at)}</div>
              </div>
            `).join('') : '<div class="label" style="text-align:center;padding:1rem">No messages yet. Start the conversation.</div>'}
          </div>
        </div>
        <div style="display:flex;gap:0.75rem">
          <input class="input" id="msg-input" placeholder="Type a message..." style="flex:1" onkeydown="if(event.key==='Enter')sendMsg(${bookingId})">
          <button class="btn btn-primary" onclick="sendMsg(${bookingId})">Send</button>
        </div>
      </div>
    `);
    // scroll to bottom
    const thread = document.getElementById('msg-thread');
    if (thread) thread.scrollTop = thread.scrollHeight;
  } catch(e) {
    setView(`<div class="page"><div class="alert alert-error">${e.message}</div></div>`);
  }
}

async function sendMsg(bookingId) {
  const input = document.getElementById('msg-input');
  const content = input.value.trim();
  if (!content) return;
  input.value = '';
  try {
    await POST(`/coaching/bookings/${bookingId}/messages`, { content });
    renderMessages([bookingId]);
  } catch(e) { toast(e.message, 'error'); input.value = content; }
}


// ── Pricing ──────────────────────────────────────────────────────
function renderPricing() {
  setView(`
    <div class="page fade-in" style="max-width:800px">
      <div class="page-header" style="text-align:center">
        <div class="page-eyebrow">Plans</div>
        <div class="page-title">Simple Pricing</div>
        <div class="page-sub">One free analysis to prove the value. Upgrade when you're ready.</div>
      </div>

      <div class="grid-2" style="gap:1.5rem;margin-bottom:3rem">
        <!-- Free -->
        <div class="panel">
          <div style="font-family:var(--display);font-size:1.8rem;margin-bottom:0.25rem">Free</div>
          <div style="font-family:var(--display);font-size:3rem;color:var(--text-1);line-height:1">$0</div>
          <div class="label" style="margin-bottom:1.5rem">Forever free</div>
          <div class="stack-sm" style="font-size:0.875rem;color:var(--text-2);margin-bottom:1.5rem">
            <div>✓ 1 analysis</div>
            <div>✓ Full OSI breakdown</div>
            <div>✓ Habit report</div>
            <div>✓ Role suggestion (after 3 sessions)</div>
            <div style="color:var(--text-3)">✗ Unlimited analyses</div>
            <div style="color:var(--text-3)">✗ Full session history</div>
            <div style="color:var(--text-3)">✗ Trend analysis</div>
          </div>
          <button class="btn btn-ghost btn-full" onclick="navigate('/register')">Get Started</button>
        </div>

        <!-- Pro -->
        <div class="panel-accent" style="position:relative">
          <div style="position:absolute;top:-12px;right:1rem">
            <span class="badge badge-accent">Most Popular</span>
          </div>
          <div style="font-family:var(--display);font-size:1.8rem;margin-bottom:0.25rem">Pro</div>
          <div style="display:flex;align-items:baseline;gap:0.25rem">
            <div style="font-family:var(--display);font-size:3rem;color:var(--accent);line-height:1">$7</div>
            <div class="label">/month</div>
          </div>
          <div class="label" style="margin-bottom:1.5rem">Cancel anytime</div>
          <div class="stack-sm" style="font-size:0.875rem;color:var(--text-2);margin-bottom:1.5rem">
            <div style="color:var(--green)">✓ Unlimited analyses</div>
            <div style="color:var(--green)">✓ Full session history</div>
            <div style="color:var(--green)">✓ Cross-session trend analysis</div>
            <div style="color:var(--green)">✓ Personalised drill plans</div>
            <div style="color:var(--green)">✓ Full rank + role certification</div>
            <div style="color:var(--green)">✓ Coach booking access</div>
          </div>
          <button class="btn btn-primary btn-full" onclick="subscribePro()">Upgrade to Pro</button>
        </div>
      </div>

      <div class="panel" style="background:var(--bg-4)">
        <div class="label label-accent" style="margin-bottom:0.75rem">For Sponsors & Advertisers</div>
        <div class="grid-2" style="gap:1.5rem">
          <div>
            
      </div>
    </div>
  `);
}

async function subscribePro() {
  if (!isAuthed()) { navigate('/login'); return; }
  try {
    const data = await POST('/payments/subscribe', {});
    if (data.checkout_url && !data.checkout_url.includes('PLACEHOLDER')) {
      window.location.href = data.checkout_url;
    } else {
      toast('Stripe not configured yet. Contact support@mechgg.gg to subscribe.', 'info');
    }
  } catch(e) { toast(e.message, 'error'); }
}

// ── Legal ────────────────────────────────────────────────────────
async function renderLegal() {
  try {
    const [terms, privacy] = await Promise.all([
      GET('/legal/terms').catch(() => ({ content: 'Terms of service coming soon.' })),
      GET('/legal/privacy').catch(() => ({ content: 'Privacy policy coming soon.' })),
    ]);
    setView(`
      <div class="page fade-in">
        <div class="page-header">
          <div class="page-eyebrow">Legal</div>
          <div class="page-title">Terms & Privacy</div>
        </div>
        <div class="tabs">
          <button class="tab active" id="tab-terms" onclick="switchLegalTab('terms')">Terms of Service</button>
          <button class="tab" id="tab-priv" onclick="switchLegalTab('priv')">Privacy Policy</button>
        </div>
        <div id="legal-terms" class="panel">
          <div style="font-size:0.875rem;color:var(--text-2);line-height:1.8;white-space:pre-wrap">${terms.content || terms}</div>
        </div>
        <div id="legal-priv" class="panel" style="display:none">
          <div style="font-size:0.875rem;color:var(--text-2);line-height:1.8;white-space:pre-wrap">${privacy.content || privacy}</div>
        </div>
      </div>
    `);
  } catch(e) {
    setView(`<div class="page"><div class="alert alert-error">${e.message}</div></div>`);
  }
}

function switchLegalTab(tab) {
  document.getElementById('legal-terms').style.display = tab==='terms' ? 'block' : 'none';
  document.getElementById('legal-priv').style.display  = tab==='priv'  ? 'block' : 'none';
  document.getElementById('tab-terms').className = 'tab' + (tab==='terms'?' active':'');
  document.getElementById('tab-priv').className  = 'tab' + (tab==='priv' ?' active':'');
}

// ── Not found ────────────────────────────────────────────────────
function renderNotFound() {
  setView(`
    <div class="page" style="text-align:center;padding-top:6rem">
      <div style="font-family:var(--display);font-size:6rem;color:var(--text-3)">404</div>
      <div style="font-family:var(--display);font-size:2rem;margin-bottom:1rem">Page not found</div>
      <button class="btn btn-primary" onclick="navigate('/')">Back to Home</button>
    </div>
  `);
}
