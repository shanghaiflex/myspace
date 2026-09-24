#!/usr/bin/env python3
"""Amnezia Premium без GUI: выдать «родной» конфиг AmneziaWG отдельному устройству (mini).

Ключ подписки `vpn://…` (AmneziaVPN → поделиться) — это не конфиг, а api_key к шлюзу gw.amnezia.org; конфиг
шлюз выдаёт каждому устройству свой (как пункт «конфиг для роутера» в приложении). Запрос шифруется так же,
как это делает приложение: AES-256-CBC со случайным ключом, сам ключ — RSA PKCS#1 публичным ключом шлюза.
Этот ключ зашит в AmneziaVPN.app, отсюда он и берётся (второй из двух PEM в бинарнике — боевой; сверено
24.09.2026 на 5.0.3). Нужен только openssl, пакета cryptography на машинах нет.

    pbpaste | python3 deploy/amnezia-premium.py info                # подписка, устройства, страны
    pbpaste | python3 deploy/amnezia-premium.py config fi > mini.conf [--uuid <uuid устройства>]

Устройство = installation uuid. Для mini он записан в deploy/README.md: перевыпуск с тем же uuid заменяет его
конфиг, а не занимает ещё одно место из семи.
"""
import base64, json, os, re, subprocess, sys, urllib.error, urllib.request, uuid, zlib

GW = "http://gw.amnezia.org:80/"
APP = "/Applications/AmneziaVPN.app/Contents/MacOS/AmneziaVPN"


def sh(args, data=b""):
    return subprocess.run(args, input=data, capture_output=True, check=True).stdout


def gateway_pem():
    text = sh(["strings", "-n", "6", APP]).decode("latin-1")
    pems = re.findall(r"-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----", text, re.S)
    if len(pems) < 2:
        raise SystemExit("в AmneziaVPN.app не нашлось ключей шлюза")
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), "amnezia-gw.pem")
    open(path, "w").write(pems[1] + "\n")
    return path


def read_key():
    s = sys.stdin.read().strip()
    if not s.startswith("vpn://"):
        raise SystemExit("на stdin нужен ключ vpn://…")
    s = s[6:]
    j = json.loads(zlib.decompress(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))[4:]))
    if "auth_data" not in j:
        raise SystemExit("это не ключ Premium (нет auth_data) — похоже, готовый конфиг")
    return j


def aes(data, key, iv, dec=False):
    return sh(["openssl", "enc", "-aes-256-cbc", "-K", key.hex(), "-iv", iv.hex()] + (["-d"] if dec else []), data)


def post(path, payload):
    key, iv, salt = os.urandom(32), os.urandom(32), os.urandom(8)
    kp = json.dumps({k: base64.b64encode(v).decode() for k, v in
                     (("aes_key", key), ("aes_iv", iv), ("aes_salt", salt))}).encode()
    ek = sh(["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", gateway_pem(),
             "-pkeyopt", "rsa_padding_mode:pkcs1"], kp)
    body = json.dumps({"key_payload": base64.b64encode(ek).decode(),
                       "api_payload": base64.b64encode(aes(json.dumps(payload).encode(), key, iv[:16])).decode()})
    req = urllib.request.Request(GW + path, body.encode(), {"Content-Type": "application/json"})
    try:
        raw = urllib.request.urlopen(req, timeout=30).read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"шлюз ответил {e.code}")
    return json.loads(aes(raw, key, iv[:16], dec=True))


def base(k):
    return {"app_version": "5.0.3", "os_version": "macos", "service_type": k["api_config"]["service_type"],
            "user_country_code": k["api_config"].get("user_country_code", "ru"), "auth_data": k["auth_data"]}


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("info", "config"):
        raise SystemExit(__doc__)
    k = read_key()
    if args[0] == "info":
        r = post("v1/account_info", base(k))
        print(f"до {r['subscription_end_date'][:10]}, устройств {r['active_device_count']} из {r['max_device_count']}")
        for c in r["issued_configs"]:
            print(f"  {c['installation_uuid']}  {c['os_version']:6} {c['server_country_code']:8} {c['last_downloaded'][:10]}")
        print("страны:", " ".join(c["server_country_code"] for c in r["available_countries"]))
        return
    country = args[1] if len(args) > 1 else "fi"
    dev = args[args.index("--uuid") + 1] if "--uuid" in args else str(uuid.uuid4())
    pem = sh(["openssl", "genpkey", "-algorithm", "X25519"])
    priv = base64.b64encode(sh(["openssl", "pkey", "-outform", "DER"], pem)[-32:]).decode()
    pub = base64.b64encode(sh(["openssl", "pkey", "-pubout", "-outform", "DER"], pem)[-32:]).decode()
    r = post("v1/native_config", dict(base(k), server_country_code=country, uuid=dev,
                                      installation_uuid=dev, public_key=pub))
    print(f"# Amnezia Premium {r.get('server_country_name', country)}, устройство {dev}", file=sys.stderr)
    sys.stdout.write(r["config"].replace("$WIREGUARD_CLIENT_PRIVATE_KEY", priv))


if __name__ == "__main__":
    main()
