/* Same-origin integration: no provider keys, no public CRM record endpoints. */
const nativeFetch = window.fetch.bind(window);
const params = new URLSearchParams(location.search);
const selected = params.get('funnel');
if (selected && /^[a-z0-9-]{1,80}$/.test(selected)) sessionStorage.setItem('re_website_funnel', selected);
const respond = (data: unknown, status = 200) => new Response(JSON.stringify(data), {status, headers:{'Content-Type':'application/json'}});
window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = new URL(input instanceof Request ? input.url : String(input), location.href);
  if (url.origin !== location.origin) return nativeFetch(input, init);
  // This import does not retain the original anonymous analytics / CRM service.
  if (['/api/analytics/event','/api/lead-magnet'].includes(url.pathname)) return respond({ok:true, recorded:false});
  if (url.pathname === '/api/analytics/summary' || (url.pathname === '/api/leads' && (init?.method || 'GET') !== 'POST')) {
    return respond({ok:false,error:'Sign in to the private CRM workspace.'},403);
  }
  if (url.pathname !== '/api/leads') return nativeFetch(input, init);
  const slug = sessionStorage.getItem('re_website_funnel');
  if (!slug || !/^[a-z0-9-]{1,80}$/.test(slug)) return respond({ok:false,error:'Preview mode: choose a published CRM funnel in Funnels & Website before collecting enquiries.'},409);
  let data;
  try { data = JSON.parse(String(init?.body || '{}')); } catch { return respond({ok:false,error:'Invalid enquiry.'},400); }
  if (data.consent !== true) return respond({ok:false,error:'Communication consent is required.'},422);
  // Reuse the platform's published funnel lookup, tenant routing and anti-bot token.
  // same-origin, not omit: a protected Vercel Preview admits only requests that carry its
  // login cookie. Public funnel routes ignore CRM session cookies and skip CSRF by design.
  const form = await nativeFetch('/f/' + encodeURIComponent(slug), {credentials:'same-origin',cache:'no-store'});
  if (!form.ok) return respond({ok:false,error:'This CRM funnel is not published or is unavailable.'},404);
  const doc = new DOMParser().parseFromString(await form.text(),'text/html');
  const ftok = doc.querySelector<HTMLInputElement>('input[name="_ftok"]')?.value;
  const result = await nativeFetch('/api/v1/public/funnel/' + encodeURIComponent(slug) + '/submit',{
    method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      name:data.fullName,phone:data.phone,email:data.email,city:'Nagpur',consent:data.consent,
      company_website:data.website || '',_ftok:ftok,
      requirement:[data.projectName,data.plotNumber ? 'Plot '+data.plotNumber : '',data.interest,data.message].filter(Boolean).join(' · ').slice(0,200),
    }),
  });
  const saved = await result.json().catch(()=>({}));
  if (!result.ok || !saved.ok) return respond({ok:false,error:saved.error?.message || 'Could not save your enquiry. Please try again.'},result.status || 502);
  return respond({ok:true,referenceCode:saved.contact_id || 'ENQUIRY-RECEIVED',lead:{crmSyncStatus:'Enquiry received — advisor confirmation pending',followUpSequence:[]}});
};
