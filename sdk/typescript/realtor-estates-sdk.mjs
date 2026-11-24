/**
 * Realtor Estates — Official ESM TypeScript / JavaScript SDK (`v1.37.0`).
 *
 * Zero-dependency client for `/api/v1`, OAuth 2.1 PKCE `S256`, and the Governed
 * Model Context Protocol (`/api/v1/mcp`) operating layer.
 */
import crypto from 'node:crypto';

export function generatePkcePair() {
  const verifier = crypto.randomBytes(36).toString('base64url');
  const challenge = crypto.createHash('sha256').update(verifier, 'ascii').digest('base64url');
  return { verifier, challenge, method: 'S256' };
}

export class RealtorEstatesClient {
  /**
   * @param {{ baseUrl: string, accessToken?: string }} opts
   */
  constructor({ baseUrl, accessToken = '' }) {
    this.baseUrl = String(baseUrl || '').replace(/\/+$/, '');
    this.accessToken = accessToken;

    this.contacts = {
      list: () => this.request('GET', '/api/v1/contacts'),
      create: ({ name, phone, city = '', requirement = '', consentGranted = true, consent = undefined }) =>
        this.request('POST', '/api/v1/contacts', {
          name,
          phone,
          city,
          requirement,
          consent:
            consent !== undefined
              ? consent
              : { status: consentGranted ? 'granted' : 'pending', purpose: 'sdk_capture', channel: 'api' },
        }),
    };

    this.properties = {
      list: () => this.request('GET', '/api/v1/properties'),
      trustPack: (propertyId) =>
        this.request('GET', `/api/v1/properties/${encodeURIComponent(propertyId)}/marketing-trust-pack`),
    };

    this.deals = {
      list: () => this.request('GET', '/api/v1/deals'),
    };

    this.govt = {
      costSheet: (payload) => this.request('POST', '/api/v1/govt/cost-sheet', payload),
    };

    this.mcp = {
      doctor: () => this.request('GET', '/api/v1/mcp/doctor'),
      listTools: async (profile = 'curated') => {
        const res = await this.request('POST', `/api/v1/mcp?profile=${encodeURIComponent(profile)}`, {
          jsonrpc: '2.0',
          id: 1,
          method: 'tools/list',
          params: { profile },
        });
        return (res.result && res.result.tools) || [];
      },
      callTool: async (name, args = {}, profile = 'curated') => {
        const res = await this.request('POST', `/api/v1/mcp?profile=${encodeURIComponent(profile)}`, {
          jsonrpc: '2.0',
          id: 2,
          method: 'tools/call',
          params: { name, arguments: args, profile },
        });
        return res.result || res;
      },
    };
  }

  async request(method, path, body = undefined) {
    const headers = { Accept: 'application/json' };
    if (this.accessToken) headers.Authorization = `Bearer ${this.accessToken}`;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const res = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const code = (data.error && data.error.code) || `HTTP_${res.status}`;
      const msg = (data.error && data.error.message) || res.statusText;
      const err = new Error(`[${code}] ${msg}`);
      err.status = res.status;
      err.code = code;
      throw err;
    }
    return data;
  }
}
