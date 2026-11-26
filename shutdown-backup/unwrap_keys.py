import base64, json, os, subprocess, sys, tempfile, time

backup = sys.argv[1]
key_id = sys.argv[2]

def copy_rows(dump, table):
    out = subprocess.run(["pg_restore", "-a", "-t", table, "-f", "-", dump], check=True, capture_output=True, text=True).stdout
    cols, rows, on = None, [], False
    for line in out.splitlines():
        if line.startswith("COPY "):
            cols = [c.strip() for c in line[line.index("(") + 1:line.index(")")].split(",")]
            on = True
        elif line == "\\.":
            on = False
        elif on:
            rows.append(dict(zip(cols, line.split("\t"))))
    return rows

wrapped = {}
for r in copy_rows(f"{backup}/db/content.dump", "project_keys"):
    wrapped.setdefault(r["project_id"], set()).add(r["wrapped_key"])
for t in ("summary_queue", "document_summaries"):
    for r in copy_rows(f"{backup}/db/ai_chat.dump", t):
        if r["wrapped_key"] != "\\N":
            wrapped.setdefault(r["project_id"], set()).add(r["wrapped_key"])

path = f"{backup}/keys/project-keys.json"
keys = json.load(open(path)) if os.path.exists(path) else {}
failed = []
for pid, ws in sorted(wrapped.items()):
    if pid in keys:
        continue
    plains = set()
    for w in ws:
        blob = bytes.fromhex(w.replace("\\\\x", ""))
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(blob)
        try:
            for attempt in range(8):
                p = subprocess.run(["aws", "kms", "decrypt", "--key-id", key_id, "--ciphertext-blob", f"fileb://{f.name}",
                                    "--encryption-context", f"project_id={pid}", "--query", "Plaintext", "--output", "text"],
                                   capture_output=True, text=True)
                if p.returncode == 0:
                    break
                time.sleep(5)
        finally:
            os.unlink(f.name)
        if p.returncode != 0:
            failed.append((pid, p.stderr.strip()[-160:]))
            continue
        plains.add(p.stdout.strip())
    if len(plains) == 1:
        k = plains.pop()
        assert len(base64.b64decode(k)) == 32
        keys[pid] = k
    elif len(plains) > 1:
        failed.append((pid, "different keys for one project"))

fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(keys, f, indent=1)
print(f"projects with wrapped keys: {len(wrapped)}, unwrapped: {len(keys)}, failed: {len(failed)}")
for x in failed[:5]:
    print("FAILED", x)
sys.exit(1 if failed else 0)
