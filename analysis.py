"""
Access Governance Anomaly Analytics
Dataset: Amazon Employee Access (public, OpenML id 4135) - 32,769 access requests.
Each row: the resource requested, the employee's manager, role hierarchy fields, and
whether access was approved (target = 1) or denied (target = 0).

Run:  python analysis.py
Outputs go to outputs/ and charts to images/.
"""
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from scipy.io import arff

ROOT = Path(__file__).parent
OUT = ROOT / "outputs"
IMG = ROOT / "images"
OUT.mkdir(exist_ok=True)
IMG.mkdir(exist_ok=True)

PEER_COL = "ROLE_DEPTNAME"  # peer group = employees in the same department
MIN_USES_ELSEWHERE = 5      # resource must be in established use outside the department


# %% 1. Load
raw, _ = arff.loadarff(ROOT / "data" / "Amazon_employee_access.arff")
df = pd.DataFrame(raw)
for c in df.columns:
    df[c] = df[c].str.decode("utf-8").astype(int)
df = df.rename(columns={"target": "APPROVED"})
print(f"Loaded {len(df):,} requests, {df.shape[1]} columns")


# %% 2. Profile + data-quality checks
profile = pd.DataFrame({
    "dtype": df.dtypes.astype(str),
    "nulls": df.isnull().sum(),
    "distinct_values": df.nunique(),
})
profile.to_csv(OUT / "01_profile.csv")

dupes = int(df.duplicated().sum())
title_to_code = df.groupby("ROLE_TITLE")["ROLE_CODE"].nunique()
code_to_title = df.groupby("ROLE_CODE")["ROLE_TITLE"].nunique()
redundant = bool((title_to_code == 1).all() and (code_to_title == 1).all())
res_counts = df["RESOURCE"].value_counts()
single_use = int((res_counts == 1).sum())

dq = {
    "rows": len(df),
    "null_cells": int(df.isnull().sum().sum()),
    "duplicate_rows": dupes,
    "distinct_resources": int(df["RESOURCE"].nunique()),
    "distinct_managers": int(df["MGR_ID"].nunique()),
    "distinct_role_titles": int(df["ROLE_TITLE"].nunique()),
    "resources_requested_once": single_use,
    "ROLE_TITLE_and_ROLE_CODE_are_1_to_1": redundant,
    "overall_approval_rate": round(float(df["APPROVED"].mean()), 4),
}
pd.Series(dq).to_csv(OUT / "02_data_quality.csv", header=["value"])
print("Data quality:", dq)


# %% 3. Peer-group analysis: access that nobody else in the department has
#    A request is a peer-group outlier when no other request from the same department
#    has ever asked for that resource, while the resource is in use 5+ times elsewhere.
pair = df.groupby([PEER_COL, "RESOURCE"]).size().rename("dept_requests_for_resource")
res_n = df.groupby("RESOURCE").size().rename("res_requests_total")
d = df.join(pair, on=[PEER_COL, "RESOURCE"]).join(res_n, on="RESOURCE")
d["dept_peers_with_resource"] = d["dept_requests_for_resource"] - 1
d["uses_elsewhere"] = d["res_requests_total"] - d["dept_requests_for_resource"]
d["peer_outlier"] = (d["dept_peers_with_resource"] == 0) & (d["uses_elsewhere"] >= MIN_USES_ELSEWHERE)

outliers = d[d["peer_outlier"]]
normal = d[~d["peer_outlier"]]
approved_outliers = outliers[outliers["APPROVED"] == 1]

peer_summary = {
    "peer_outlier_requests": len(outliers),
    "peer_outlier_share": round(len(outliers) / len(d), 4),
    "approved_outliers_to_review": len(approved_outliers),
    "denial_rate_outliers": round(float(1 - outliers["APPROVED"].mean()), 4),
    "denial_rate_normal": round(float(1 - normal["APPROVED"].mean()), 4),
}
peer_summary["denial_rate_lift"] = round(
    peer_summary["denial_rate_outliers"] / peer_summary["denial_rate_normal"], 2)
pd.Series(peer_summary).to_csv(OUT / "03_peer_group_summary.csv", header=["value"])
print("Peer-group analysis:", peer_summary)


# %% 4. Risk register for approved outlier access (what a reviewer should look at first)
res_denial = df.groupby("RESOURCE")["APPROVED"].agg(res_requests="size", res_approval="mean")
mgr_out = (approved_outliers.groupby("MGR_ID").size().rename("mgr_outlier_approvals"))
reg = (approved_outliers
       .join(res_denial, on="RESOURCE")
       .join(mgr_out, on="MGR_ID"))

# Each signal scaled 0-1, higher = riskier
reg["s_rarity"] = reg["uses_elsewhere"].rank(pct=True)               # widely used by other depts, never here
reg["s_resource_denied"] = 1 - reg["res_approval"]                   # resource often denied to others
reg["s_manager_pattern"] = (reg["mgr_outlier_approvals"].rank(pct=True))  # manager approves many outliers
reg["risk_score"] = (0.5 * reg["s_rarity"] + 0.3 * reg["s_resource_denied"]
                     + 0.2 * reg["s_manager_pattern"]).round(3)
reg["severity"] = pd.cut(reg["risk_score"], bins=[-0.01, 0.45, 0.6, 1.0],
                         labels=["Low", "Medium", "High"])
reg = reg.sort_values("risk_score", ascending=False)
cols = ["RESOURCE", "MGR_ID", "ROLE_DEPTNAME", "ROLE_TITLE", "uses_elsewhere",
        "res_requests", "res_approval", "mgr_outlier_approvals",
        "risk_score", "severity"]
reg[cols].to_csv(OUT / "04_risk_register.csv", index=False)
sev = reg["severity"].value_counts().reindex(["High", "Medium", "Low"])
print("Risk register severity counts:", sev.to_dict())

# Manager concentration: how much of the outlier access sits with a few managers
mgr_sorted = mgr_out.sort_values(ascending=False)
top10_share = round(mgr_sorted.head(10).sum() / mgr_sorted.sum(), 4)
pd.Series({"managers_with_outlier_approvals": len(mgr_sorted),
           "top_10_managers_share_of_outlier_approvals": top10_share}
          ).to_csv(OUT / "05_manager_concentration.csv", header=["value"])
print("Top-10 managers hold", f"{top10_share:.1%}", "of approved outlier access")


# %% 5. SQL checks (SQLite) - same findings reproduced in SQL
con = sqlite3.connect(":memory:")
df.to_sql("requests", con, index=False)
sql = (ROOT / "queries.sql").read_text().split(";")
with open(OUT / "06_sql_results.txt", "w") as f:
    for q in [s.strip() for s in sql if s.strip()]:
        res = pd.read_sql_query(q, con)
        f.write(q + "\n" + res.to_string(index=False) + "\n\n")
con.close()


# %% 6. Charts
plt.rcParams.update({"font.size": 10})

fig, ax = plt.subplots(figsize=(6, 4))
rates = [peer_summary["denial_rate_normal"] * 100, peer_summary["denial_rate_outliers"] * 100]
bars = ax.bar(["Normal access", "No department peer has it"], rates, color=["#7A8CA5", "#C0392B"])
for b, r in zip(bars, rates):
    ax.text(b.get_x() + b.get_width() / 2, r + 0.5, f"{r:.1f}%", ha="center")
ax.set_ylabel("Requests denied (%)")
ax.set_ylim(0, max(rates) * 1.2)
ax.set_title("Cross-department outlier requests get denied more")
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout(); fig.savefig(IMG / "denial_rate_outliers.png", dpi=150); plt.close(fig)

fig, ax = plt.subplots(figsize=(6, 4))
sev.plot.bar(ax=ax, color=["#C0392B", "#E67E22", "#7A8CA5"], rot=0)
for i, v in enumerate(sev.values):
    ax.text(i, v + max(sev.values) * 0.01, f"{v:,}", ha="center")
ax.set_ylabel("Approved outlier requests")
ax.set_ylim(0, max(sev.values) * 1.15)
ax.set_title("Risk register: approved access to review")
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout(); fig.savefig(IMG / "risk_register_severity.png", dpi=150); plt.close(fig)

fig, ax = plt.subplots(figsize=(6, 4))
cum = mgr_sorted.cumsum() / mgr_sorted.sum() * 100
ax.plot(range(1, len(cum) + 1), cum.values, color="#1F4E79")
ax.axvline(10, color="#C0392B", ls="--", lw=1)
ax.text(12, cum.iloc[min(9, len(cum) - 1)] - 8, f"Top 10 managers = {top10_share:.0%}", color="#C0392B")
ax.set_xlabel("Managers (ranked by approved outlier access)")
ax.set_ylabel("Cumulative share (%)")
ax.set_title("Outlier approvals are spread across many managers")
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout(); fig.savefig(IMG / "manager_concentration.png", dpi=150); plt.close(fig)

print("Done. See outputs/ and images/.")
