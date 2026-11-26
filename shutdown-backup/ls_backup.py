import base64, hashlib, hmac, json, os, subprocess, zlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS = {p: base64.b64decode(k) for p, k in json.load(open(f"{ROOT}/keys/project-keys.json")).items()}

_ESC = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\"}

def _field(v):
    if v == "\\N":
        return None
    if "\\" not in v:
        return v
    out, i = [], 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            out.append(_ESC.get(v[i + 1], v[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)

def rows(db, table):
    out = subprocess.run(["pg_restore", "-a", "-t", table, "-f", "-", f"{ROOT}/db/{db}.dump"],
                         check=True, capture_output=True, text=True).stdout
    cols, on = None, False
    for line in out.split("\n"):
        if line.startswith("COPY "):
            cols = [c.strip().strip('"') for c in line[line.index("(") + 1:line.index(")")].split(",")]
            on = True
        elif line == "\\.":
            on = False
        elif on:
            yield dict(zip(cols, [_field(x) for x in line.split("\t")]))

def blob(v):
    return None if v is None else bytes.fromhex(v[2:])

def open_sealed(project_id, subject_id, purpose, data):
    assert data[0] == 1
    aad = f"lore-sentry:v1:{project_id.lower()}:{subject_id.lower()}:{purpose}".encode()
    return zlib.decompress(AESGCM(KEYS[project_id]).decrypt(data[1:13], data[13:], aad)).decode("utf-8")

def body_mac(project_id, text):
    mac_key = hmac.new(KEYS[project_id], b"lore-sentry:mac:v1", hashlib.sha256).digest()
    return hmac.new(mac_key, text.encode("utf-8"), hashlib.sha256).digest()
