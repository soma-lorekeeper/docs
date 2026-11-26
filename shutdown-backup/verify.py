import collections, json, sys
from ls_backup import KEYS, rows, blob, open_sealed, body_mac

ok, bad = collections.Counter(), []

def check(label, project_id, subject_id, purpose, data, after=None):
    try:
        text = open_sealed(project_id, subject_id, purpose, data)
        if after:
            after(text)
        ok[label] += 1
        return text
    except Exception as e:
        bad.append((label, project_id, subject_id, type(e).__name__, str(e)[:80]))

projects = {r["id"] for r in rows("content", "projects")}
doc_project = {}
for d in rows("content", "document"):
    doc_project[d["id"]] = d["project_id"]
    p = d["project_id"]
    if d["body_json_enc"]:
        check("document.body_json", p, d["id"], "body_json", blob(d["body_json_enc"]), json.loads)
    if d["body_md_enc"]:
        check("document.body_md", p, d["id"], "body_md", blob(d["body_md_enc"]))
    def text_ok(t, d=d, p=p):
        assert len(t) == int(d["body_text_length"]), "length"
        assert body_mac(p, t) == blob(d["body_text_mac"]), "mac"
    check("document.body_text", p, d["id"], "body_text", blob(d["body_text_enc"]), text_ok)

for v in rows("content", "document_versions"):
    check("document_versions.snapshot", doc_project[v["document_id"]], v["document_id"], "version_snapshot", blob(v["snapshot_enc"]))

run_project = {r["id"]: r["project_id"] for r in rows("content", "refresh_runs")}
for dr in rows("content", "refresh_document_drafts"):
    p = run_project[dr["refresh_run_id"]]
    for side in ("left", "right", "origin"):
        if dr[f"{side}_snapshot_enc"]:
            check(f"drafts.{side}", p, dr["id"], f"draft_{side}", blob(dr[f"{side}_snapshot_enc"]))
results = list(rows("content", "refresh_results"))

for t, col, purpose in (("summary_queue", "body_enc", "body_text"), ("document_summaries", "summary_enc", "summary")):
    for r in rows("ai_chat", t):
        if r[col]:
            check(f"ai_chat.{t}", r["project_id"], r["document_id"], purpose, blob(r[col]))

print("projects:", len(projects), "keys:", len(KEYS), "documents:", len(doc_project))
print("projects with documents but no key:", len({p for p in doc_project.values()} - set(KEYS)))
for k, n in sorted(ok.items()):
    print(f"  ok {k}: {n}")
print("failed:", len(bad))
for b in bad[:10]:
    print("  ", b)
print("refresh_results (not verified):", len(results))
missing = {p for p in doc_project.values()} - set(KEYS)
sys.exit(1 if bad or missing else 0)
