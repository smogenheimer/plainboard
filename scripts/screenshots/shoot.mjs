// Takes the README's screenshots from the demo instance; how to set it up is in seed_api.py.
// usage: BASE=http://127.0.0.1:8080 OUT=shots node shoot.mjs [name prefix ...]
import { chromium } from 'playwright';
const base = process.env.BASE || 'http://127.0.0.1:8080';
const out = process.env.OUT || 'shots';
const only = process.argv.slice(2);
const b = await chromium.launch({ executablePath: process.env.CHROME, args: ['--no-sandbox'] });
const HIDE = '[title="Runner not reachable"]{display:none!important}';
async function shot(name, { view = 'board', theme = 'dark', phone = false, chat = true, w = 1600, h = 1000, clip, section = 'general', steps = async () => {} }) {
  if (only.length && !only.some((o) => name.startsWith(o))) return;
  const ctx = await b.newContext({ viewport: phone ? { width: 390, height: 844 } : { width: w, height: h }, isMobile: phone, hasTouch: phone, deviceScaleFactor: 2, colorScheme: theme, locale: 'en-US', timezoneId: 'Europe/Berlin' });
  await ctx.request.post(base + '/api/auth/login', { data: { username: 'mia', password: 'demo-pass-1234' }, headers: { Origin: base } });
  const p = await ctx.newPage();
  await p.goto(base + '/api/health');
  await p.evaluate(([view, chat, section]) => {
    localStorage.setItem('plainboard.project', '1');
    localStorage.setItem('plainboard.view', JSON.stringify(view));
    localStorage.setItem('plainboard.chatOpen', JSON.stringify(chat));
    localStorage.setItem('plainboard.panelWidth', '420');
    localStorage.setItem('plainboard.notifyAsked', 'true');
    localStorage.setItem('plainboard.projectSection', JSON.stringify(section));
  }, [view, chat, section]);
  await p.goto(base + '/');
  await p.addStyleTag({ content: HIDE });
  await p.waitForTimeout(2000);
  await steps(p);
  await p.waitForTimeout(600);
  await p.screenshot({ path: `${out}/${name}.png`, clip });
  console.log('shot', name);
  await ctx.close();
}
const openTicket = (title) => async (p) => { await p.locator('article').filter({ hasText: title }).first().click(); await p.waitForTimeout(1200); };
for (const theme of ['dark', 'light']) {
  await shot(`board-${theme}`, { theme });
  await shot(`chat-${theme}`, { theme, view: 'chat' });
  await shot(`ideas-${theme}`, { theme, view: 'ideas', chat: false });
  await shot(`flow-${theme}`, { theme, view: 'flow', chat: false });
  await shot(`ticket-${theme}`, { theme, chat: false, steps: openTicket('Pizza night planner') });
  await shot(`members-${theme}`, { theme, view: 'project', section: 'members', chat: false, w: 1280, h: 1000, clip: { x: 0, y: 0, width: 1280, height: 560 } });
  await shot(`phone-board-${theme}`, { theme, phone: true });
  await shot(`phone-chat-${theme}`, { theme, phone: true, view: 'chat' });
  await shot(`phone-ticket-${theme}`, { theme, phone: true, steps: openTicket('Pizza night planner') });
}
await b.close();
