import os, re, sys
from ls_backup import ROOT, rows, blob, open_sealed

who = sys.argv[1].strip().lower()
out_root = sys.argv[2] if len(sys.argv) > 2 else f"{ROOT}/exports"

ids = [r for r in rows("authentication", "oauth_identities")
       if (r["email"] or "").lower() == who or (r["provider"] == "password" and r["provider_id"].lower() == who)]
if not ids:
    sys.exit(f"no account for {who}")
user_id = ids[0]["user_id"]

def safe(name):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip()[:120] or "untitled"

folders = {r["id"]: r for r in rows("content", "base_folders")}
folder_name = lambda fid: folders[fid]["name"] if fid in folders else str(fid)
episodes = {r["id"]: r.get("name") or r.get("title") or r["id"] for r in rows("content", "episode_folders")}
projects = {r["id"]: r for r in rows("content", "projects") if r["owner_user_id"] == user_id}

base = os.path.join(out_root, safe(who))
written = 0
for d in rows("content", "document"):
    p = projects.get(d["project_id"])
    if not p:
        continue
    text = open_sealed(d["project_id"], d["id"], "body_text", blob(d["body_text_enc"]))
    parts = [base, safe(p["name"]) + (" (휴지통)" if p["trashed_at"] else ""), safe(folder_name(d["folder_id"]))]
    if d["episode_id"]:
        parts.append(safe(episodes.get(d["episode_id"], d["episode_id"])))
    if d["trashed_at"]:
        parts.append("휴지통")
    os.makedirs(os.path.join(*parts), exist_ok=True)
    path = os.path.join(*parts, safe(d["title"]) + ".txt")
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(*parts, f"{safe(d['title'])} ({n}).txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    written += 1
print(f"{who}: projects {len(projects)}, documents {written} -> {base}")
