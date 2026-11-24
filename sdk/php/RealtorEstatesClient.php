<?php
declare(strict_types=1);

namespace RealtorEstates\Sdk;

/**
 * Realtor Estates — Official Type-Safe PHP 8.1+ SDK (`v1.37.0`).
 *
 * Covers Contacts, Properties, Deals, Funnels, Invoices, Maharashtra Govt
 * Cost-Sheets, OAuth 2.1 PKCE `S256`, and the Governed MCP Operating Layer
 * (parity with `gohighlevel-php-sdk` + Zero-Trust PKCE/MCP safety).
 */
final class RealtorEstatesClient
{
    private string $baseUrl;
    private string $accessToken;
    private int $timeoutSec;

    public function __construct(string $baseUrl, string $accessToken = '', int $timeoutSec = 10)
    {
        $this->baseUrl = rtrim($baseUrl, '/');
        $this->accessToken = $accessToken;
        $this->timeoutSec = max(2, $timeoutSec);
    }

    /**
     * @return array{verifier: string, challenge: string, method: string}
     */
    public static function generatePkcePair(): array
    {
        $verifier = rtrim(strtr(base64_encode(random_bytes(36)), '+/', '-_'), '=');
        $challenge = rtrim(strtr(base64_encode(hash('sha256', $verifier, true)), '+/', '-_'), '=');
        return ['verifier' => $verifier, 'challenge' => $challenge, 'method' => 'S256'];
    }

    /** @return array<string, mixed> */
    public function listContacts(): array
    {
        return $this->request('GET', '/api/v1/contacts');
    }

    /** @return array<string, mixed> */
    public function createContact(string $name, string $phone, string $city = '', bool $consentGranted = true): array
    {
        return $this->request('POST', '/api/v1/contacts', [
            'name' => $name,
            'phone' => $phone,
            'city' => $city,
            'consent' => [
                'status' => $consentGranted ? 'granted' : 'pending',
                'purpose' => 'php_sdk_capture',
                'channel' => 'api',
            ],
        ]);
    }

    /** @return array<string, mixed> */
    public function listProperties(): array
    {
        return $this->request('GET', '/api/v1/properties');
    }

    /** @return array<string, mixed> */
    public function listDeals(): array
    {
        return $this->request('GET', '/api/v1/deals');
    }

    /**
     * @param array<string, mixed> $payload
     * @return array<string, mixed>
     */
    public function computeCostSheet(array $payload): array
    {
        return $this->request('POST', '/api/v1/govt/cost-sheet', $payload);
    }

    /** @return array<string, mixed> */
    public function mcpDoctor(): array
    {
        return $this->request('GET', '/api/v1/mcp/doctor');
    }

    /**
     * @param array<string, mixed> $arguments
     * @return array<string, mixed>
     */
    public function callMcpTool(string $name, array $arguments = [], string $profile = 'curated'): array
    {
        return $this->request('POST', '/api/v1/mcp?profile=' . rawurlencode($profile), [
            'jsonrpc' => '2.0',
            'id' => 1,
            'method' => 'tools/call',
            'params' => ['name' => $name, 'arguments' => $arguments, 'profile' => $profile],
        ]);
    }

    /**
     * @param array<string, mixed>|null $body
     * @return array<string, mixed>
     */
    public function request(string $method, string $path, ?array $body = null): array
    {
        $headers = ["Accept: application/json"];
        if ($this->accessToken !== '') {
            $headers[] = "Authorization: Bearer {$this->accessToken}";
        }
        $content = null;
        if ($body !== null) {
            $headers[] = "Content-Type: application/json";
            $content = json_encode($body, JSON_THROW_ON_ERROR);
        }
        $ctx = stream_context_create([
            'http' => [
                'method' => strtoupper($method),
                'header' => implode("\r\n", $headers),
                'content' => $content ?? '',
                'timeout' => $this->timeoutSec,
                'ignore_errors' => true,
            ],
        ]);
        $raw = file_get_contents($this->baseUrl . $path, false, $ctx);
        if ($raw === false) {
            throw new \RuntimeException("Network request failed for {$path}");
        }
        /** @var array<string, mixed> $decoded */
        $decoded = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
        return $decoded;
    }
}
