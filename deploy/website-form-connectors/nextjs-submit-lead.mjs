/**
 * Drop-in Website Form Backend Handler (Next.js / Vercel / Netlify / Lovable / Bolt)
 * Set environment variables on your website host:
 *   CRM_BASE_URL   = https://crm.yourdomain.com
 *   CRM_INGEST_KEY = <minted via POST /api/v1/tenant/ingest-keys>
 */
export default async function submitLeadHandler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'Method not allowed' });
  }
  const body = req.body || {};
  const upstream = await fetch(`${process.env.CRM_BASE_URL}/api/v1/ingest/website`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Ingest-Key': process.env.CRM_INGEST_KEY || '',
    },
    body: JSON.stringify({
      name: body.name || body.full_name || '',
      phone: body.phone || body.mobile || '',
      email: body.email || '',
      city: body.city || '',
      locality: body.locality || '',
      budget: body.budget || '',
      property_type: body.property_type || '',
      requirement: body.message || body.requirement || 'Website enquiry',
      utm_campaign: body.utm_campaign || 'website-form',
    }),
  });
  const data = await upstream.json();
  return res.status(upstream.status).json(data);
}
