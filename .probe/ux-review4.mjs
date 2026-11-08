import { createRequire } from 'node:module'
const require = createRequire('D:/Desktop/MyProjects/AI_projects/DocMind/_scratch/wt-firsthome/frontend/package.json')
const { chromium } = require('@playwright/test')
const BASE='http://localhost:5187'
const sleep=ms=>new Promise(r=>setTimeout(r,ms))
const USER={id:1,username:'demo_reviewer',email:'demo@example.com',fullName:'演示审阅员',nickname:'演示审阅员',isAdmin:false,is_superuser:false,is_org_admin:false,organization_id:1,createdAt:'2026-01-01T00:00:00Z',organizations:[{id:1,name:'演示组织'}],preferences:{theme:'light'}}
const json=(route,p)=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(p)})
const browser=await chromium.launch({executablePath:'C:/Users/lenovo/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe'})
const ctx=await browser.newContext({viewport:{width:1440,height:900}})
await ctx.addInitScript(({user})=>{localStorage.setItem('docmind_token','fake');localStorage.setItem('docmind_token_expires',String(Date.now()+7*86400000));localStorage.setItem('user_info',JSON.stringify(user));localStorage.setItem('theme','light');localStorage.setItem('language','zh')},{user:USER})
const page=await ctx.newPage()
await page.route('**/api/v1/**',async route=>{const p=new URL(route.request().url()).pathname;if(p.endsWith('/chat/stream')){await new Promise(()=>{});return}if(p.endsWith('/users/me'))return json(route,{success:true,data:USER});if(p.endsWith('/users/me/organizations'))return json(route,{success:true,data:{organizations:[],current_organization_id:null}});if(p.endsWith('/user/settings'))return json(route,{success:true,data:{}});if(p.endsWith('/notifications/unread-count'))return json(route,{success:true,data:{count:0}});if(/\/conversations(\?|$)/.test(p))return json(route,{success:true,data:{data:[],total:0,page:1,page_size:50}});return json(route,{success:true,data:null})})
await page.goto(BASE+'/chat',{waitUntil:'domcontentloaded'})
await page.waitForSelector('.composer-card textarea',{timeout:30000})
await sleep(500)
const out={}
const state=()=>page.evaluate(()=>{
  const pops=[...document.querySelectorAll('.n-popover')].filter(el=>el.offsetParent!==null)
  const a=document.activeElement
  return {popovers:pops.length, popText:pops[0]?.textContent.trim().slice(0,40)||'', active:a?{tag:a.tagName,aria:a.getAttribute?.('aria-label'),text:(a.textContent||'').trim().slice(0,16),cls:String(a.className||'').slice(0,30)}:null, lang:document.documentElement.lang}
})
const globe=page.locator('button[aria-label="语言设置"]').first()
// 1) open with keyboard
await globe.focus(); await page.keyboard.press('Enter'); await sleep(350)
out.openedByEnter=await state()
// 2) Escape
await page.keyboard.press('Escape'); await sleep(300)
out.afterEscape=await state()
// 3) open, then Tab x3
if(!(await page.evaluate(()=>[...document.querySelectorAll('.n-popover')].some(el=>el.offsetParent!==null)))){await globe.focus();await page.keyboard.press('Enter');await sleep(350)}
out.tabSequence=[]
for(let i=0;i<5;i++){await page.keyboard.press('Tab');await sleep(150);out.tabSequence.push(await state())}
// 4) if an option is focused, press Enter to choose
const focusInfo=out.tabSequence[out.tabSequence.length-1].active
out.beforeEnter=await state()
await page.keyboard.press('Enter'); await sleep(1000)
out.afterEnter=await state()
await browser.close()
console.log(JSON.stringify(out,null,2))
