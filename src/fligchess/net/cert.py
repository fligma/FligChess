"""Self-signed TLS certificates, made and renewed without any dependency.

The hosted games can serve HTTPS, which needs a certificate. No CA will
sign for a bare LAN address, and the app ships no crypto library, so this
module writes one itself: an RSA key generated in pure Python and a minimal
X.509 v3 certificate carrying it, PEM-encoded the way ssl expects. OpenSSL
(through Python's own `ssl`) is the strict judge of the result - a context
that loads is a certificate that parses.

Certificates expire, that is the point of `ensure()`: the server asks for
one on its way up and gets the stored file when it is still good, a brand
new key and certificate when it has lapsed, is corrupt, or when the
requested size, lifetime or host names have changed.

`fingerprint()` is what a connecting app pins to: a self-signed cert is
unsigned by anyone the client trusts, so the client accepts the exact bytes
it saw the first time it met this host (trust on first use) and refuses a
different key later.
"""

import base64
import hashlib
import json
import random
import socket
import ssl
import time
from pathlib import Path

from fligchess.core.paths import config_dir

# defaults: 2048-bit RSA is comfortable to generate in pure Python and
# 825 days is the longest lifetime browsers accept from a server cert
DEFAULT_BITS = 2048
DEFAULT_DAYS = 825
RENEW_WITHIN = 30 * 86400        # renew early once this little time is left

OID_RSA = "1.2.840.113549.1.1.1"
OID_SHA256_RSA = "1.2.840.113549.1.1.11"
OID_CN = "2.5.4.3"
OID_SAN = "2.5.29.17"
OID_BASIC = "2.5.29.19"

# SHA256 DigestInfo prefix, the ASN.1 wrapper PKCS#1 v1.5 signs over
DIGEST_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


# ---------------------------------------------------------------- DER writing

def _length(n):
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag, payload):
    return bytes([tag]) + _length(len(payload)) + payload


def _seq(*items):
    return _tlv(0x30, b"".join(items))


def _set(*items):
    return _tlv(0x31, b"".join(items))


def _int(value):
    if value == 0:
        return _tlv(0x02, b"\x00")
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw                  # keep INTEGER unsigned
    return _tlv(0x02, raw)


def _oid(dotted):
    parts = [int(x) for x in dotted.split(".")]
    body = bytearray([parts[0] * 40 + parts[1]])
    for part in parts[2:]:
        chunk = [part & 0x7F]
        part >>= 7
        while part:
            chunk.insert(0, (part & 0x7F) | 0x80)
            part >>= 7
        body.extend(chunk)
    return _tlv(0x06, bytes(body))


def _utf8(text):
    return _tlv(0x0C, text.encode("utf-8"))


def _utc(epoch):
    return _tlv(0x17, time.strftime("%y%m%d%H%M%SZ",
                                    time.gmtime(epoch)).encode())


def _null():
    return _tlv(0x05, b"")


def _bitstr(payload):
    return _tlv(0x03, b"\x00" + payload)


def _alg(oid):
    return _seq(_oid(oid), _null())


def _name(common):
    """A one-RDN distinguished name, just a common name."""
    return _seq(_set(_seq(_oid(OID_CN), _utf8(common))))


def _san(hosts):
    """Subject alternative names: DNS for host names, raw octets for IPs."""
    names = []
    for host in hosts:
        try:
            names.append(_tlv(0x87, socket.inet_aton(host)))
        except OSError:
            names.append(_tlv(0x82, host.encode()))
    return _seq(_oid(OID_SAN), _octstr(_seq(*names)))


def _octstr(payload):
    return _tlv(0x04, payload)


# ------------------------------------------------------------------- RSA keys

_SMALL_PRIMES = [p for p in range(3, 100) if all(p % d for d in range(2, int(p ** 0.5) + 1))]


def _probably_prime(n):
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(16):                      # sixteen rounds is plenty here
        a = random.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits):
    while True:
        cand = random.getrandbits(bits) | (1 << (bits - 1)) | 1
        if all(cand % p for p in _SMALL_PRIMES) and _probably_prime(cand):
            return cand


def _modinv(e, phi):
    """Invert e mod phi with the extended Euclidean algorithm."""
    old_r, r, old_s, s = e, phi, 1, 0
    while r:
        q = old_r // r
        old_r, r = r, old_r - q * r
        old_s, s = s, old_s - q * s
    return old_s % phi


def make_key(bits=DEFAULT_BITS):
    """A fresh RSA key as a dict of its numbers."""
    e = 65537
    while True:
        p, q = _gen_prime(bits // 2), _gen_prime(bits - bits // 2)
        if p == q or (p - 1) % e == 0 or (q - 1) % e == 0:
            continue
        n = p * q
        lcm = (p - 1) * (q - 1) // _gcd(p - 1, q - 1)
        d = _modinv(e, lcm)
        if (n.bit_length() == bits and d > 1 and (d * e) % lcm == 1):
            break
    return {"n": n, "e": e, "d": d, "p": p, "q": q,
            "dp": d % (p - 1), "dq": d % (q - 1), "qn": _modinv(q, p)}


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def _sign(digest, key):
    """RSASSA-PKCS1-v1_5 signature over a SHA-256 digest."""
    k = (key["n"].bit_length() + 7) // 8
    data = DIGEST_PREFIX + digest
    pad = b"\xff" * (k - len(data) - 3)
    em = b"\x00\x01" + pad + b"\x00" + data
    return pow(int.from_bytes(em, "big"), key["d"], key["n"]).to_bytes(k, "big")


# ----------------------------------------------------------------- the cert

def _spki(key):
    return _seq(_alg(OID_RSA), _bitstr(_seq(_int(key["n"]), _int(key["e"]))))


def build_cert(key, hosts, days=DEFAULT_DAYS, serial=None):
    """Sign and return the certificate DER. CA:FALSE, valid from yesterday."""
    now = int(time.time())
    nb, na = now - 86400, now + days * 86400
    subject = _name("FligChess " + (hosts[0] if hosts else "LAN"))
    # the [3] wrapper holds one Extensions SEQUENCE OF, listing each
    # extension directly - wrapping them in an extra SEQUENCE makes OpenSSL
    # reject the whole certificate
    extensions = _seq(_san(hosts),
                      _seq(_oid(OID_BASIC), _octstr(_seq())))
    tbs = _seq(_tlv(0xA0, _int(2)), _int(serial or random.getrandbits(60) | 1),
               _alg(OID_SHA256_RSA), subject, _seq(_utc(nb), _utc(na)),
               subject, _spki(key), _tlv(0xA3, extensions))
    sig = _sign(hashlib.sha256(tbs).digest(), key)
    return _seq(tbs, _alg(OID_SHA256_RSA), _bitstr(sig))


def _pem(label, der):
    lines = [base64.b64encode(der)[i:i + 64].decode()
             for i in range(0, len(base64.b64encode(der)), 64)]
    return "-----BEGIN %s-----\n%s\n-----END %s-----\n" % (label, "\n".join(lines), label)


def _key_pem(key):
    """The private key as PKCS#8, what ssl.load_cert_chain wants."""
    sec1 = _seq(_int(0), _int(key["n"]), _int(key["e"]), _int(key["d"]),
                _int(key["p"]), _int(key["q"]), _int(key["dp"]),
                _int(key["dq"]), _int(key["qn"]))
    return _pem("PRIVATE KEY", _seq(_int(0), _alg(OID_RSA), _octstr(sec1)))


def fingerprint(der):
    return hashlib.sha256(der).hexdigest()


def _unpem(text, label):
    body = "".join(line for line in text.splitlines()
                   if "BEGIN" not in line and "END" not in line)
    return base64.b64decode(body)


# ------------------------------------------------------------ file plumbing

def tls_dir():
    return config_dir() / "tls"


def current_hosts(ip=None):
    """Names the certificate vouches for: loopback, this machine's LAN
    address, and the name browsers try first."""
    from fligchess.net.host import local_ip
    return ["localhost", "127.0.0.1", ip or local_ip()]


def _meta_path():
    return tls_dir() / "meta.json"


def load_meta():
    try:
        return json.loads(_meta_path().read_text())
    except (OSError, ValueError):
        return None


def _needs_reissue(meta, bits, days, hosts, force):
    if force or meta is None:
        return True
    if (meta.get("bits"), meta.get("days")) != (bits, days):
        return True
    if meta.get("hosts") != hosts:
        return True
    try:
        if int(meta.get("not_after", 0)) - time.time() < RENEW_WITHIN:
            return True
        tls_dir().joinpath("cert.pem").stat()
        tls_dir().joinpath("key.pem").stat()
    except (OSError, TypeError, ValueError):
        return True
    return False


def issue(bits=DEFAULT_BITS, days=DEFAULT_DAYS, hosts=None, force=True):
    """Generate a key and certificate, write them out, return the info."""
    hosts = hosts or current_hosts()
    key = make_key(bits)
    der = build_cert(key, hosts, days)
    cert_pem = _pem("CERTIFICATE", der)
    tls_dir().mkdir(parents=True, exist_ok=True)
    (tls_dir() / "cert.pem").write_text(cert_pem)
    (tls_dir() / "key.pem").write_text(_key_pem(key))
    info = {"bits": bits, "days": days, "hosts": hosts,
            "issued": int(time.time()),
            "not_after": int(time.time()) + days * 86400,
            "fingerprint": fingerprint(der),
            "cert": str(tls_dir() / "cert.pem"),
            "key": str(tls_dir() / "key.pem")}
    _meta_path().write_text(json.dumps(info, indent=2))
    # prove the result to OpenSSL before anyone tries to serve it
    server_context(info)
    return info


def ensure(bits=DEFAULT_BITS, days=DEFAULT_DAYS, hosts=None, force=False):
    """The stored certificate when it is still good, a fresh one otherwise."""
    hosts = hosts or current_hosts()
    meta = load_meta()
    if not _needs_reissue(meta, bits, days, hosts, force):
        return meta
    return issue(bits, days, hosts, force=True)


def server_context(info):
    """A server-side TLS context holding the certificate and its key."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(info["cert"], info["key"])
    return ctx


def client_context():
    """A client-side context for connecting to our own servers: OpenSSL is
    told to accept the untrusted chain, and the caller pins the certificate
    fingerprint instead - who to trust is decided on the app's terms."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
