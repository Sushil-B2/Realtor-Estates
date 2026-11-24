<?php
/**
 * Drop-in PHP Website Form Handler (Hostinger / Bluehost / IONOS / WordPress)
 * Configure environment variables on your web host:
 *   CRM_BASE_URL   = https://crm.yourdomain.com
 *   CRM_INGEST_KEY = <minted via POST /api/v1/tenant/ingest-keys>
 */
$base = rtrim(getenv("CRM_BASE_URL") ?: "https://crm.yourdomain.com", "/");
$ingestHeader = "X-Ingest-Key: " . (getenv("CRM_INGEST_KEY") ?: "");

$payload = json_encode([
  "name"          => $_POST["name"] ?? "",
  "phone"         => $_POST["phone"] ?? "",
  "email"         => $_POST["email"] ?? "",
  "city"          => $_POST["city"] ?? "",
  "budget"        => $_POST["budget"] ?? "",
  "property_type" => $_POST["property_type"] ?? "",
  "requirement"   => $_POST["message"] ?? "Website enquiry",
  "utm_campaign"  => $_POST["utm_campaign"] ?? "website-form",
]);

$ch = curl_init($base . "/api/v1/ingest/website");
curl_setopt_array($ch, [
  CURLOPT_POST           => true,
  CURLOPT_RETURNTRANSFER => true,
  CURLOPT_HTTPHEADER     => ["Content-Type: application/json", $ingestHeader],
  CURLOPT_POSTFIELDS     => $payload,
  CURLOPT_TIMEOUT        => 10,
]);
$out = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE) ?: 502;
curl_close($ch);

http_response_code($code);
header("Content-Type: application/json; charset=utf-8");
echo $out ?: '{"ok":false}';
