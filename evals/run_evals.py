"""Offline eval: python -m evals.run_evals
Reports hit-rate@k (retrieval), abstention accuracy (incl. ACL-forbidden questions) and LLM-judge scores.
Add a golden set per doc type (policy / code / log) before trusting any threshold."""
import asyncio
import json
from pathlib import Path

from app.api.v1.schemas.auth_schemas import UserContext
from app.services import mongodb_service as mdb, observability_service as obs, rag_engine


async def run():
    items = json.loads((Path(__file__).parent / "golden_dataset.json").read_text())
    hits = abst_ok = n_exp = 0
    faith: list[float] = []
    for it in items:
        u = await mdb.col("users").find_one({"_id": it["email"]})
        user = UserContext(user_id=u["_id"], email=u["email"], org_id=u["org_id"], role=u["role"],
                           clearance_level=u["clearance_level"], groups=u.get("groups", []))
        ctx, done, ans = [], {}, ""
        async for ev, data in rag_engine.answer_stream(user, it["question"]):
            if ev == "context": ctx = data
            elif ev == "token": ans += data
            elif ev == "done": done = data
        abst_ok += int(done["abstained"] == it["should_abstain"])
        if not it["should_abstain"]:
            n_exp += 1
            hits += int(any(c["title"] in it["expected_titles"] for c in ctx))
            await obs.run_sampled_eval(done["log_id"], it["question"], ans, [c["snippet"] for c in ctx], force=True)
            row = await mdb.col("query_logs").find_one({"trace_id": done["trace_id"]})
            if row and row.get("eval"): faith.append(row["eval"]["faithfulness"])
    print(json.dumps({"n": len(items), "hit_rate": hits / max(n_exp, 1), "abstain_accuracy": abst_ok / len(items),
                      "avg_faithfulness": sum(faith) / len(faith) if faith else None}, indent=2))

asyncio.run(run())
