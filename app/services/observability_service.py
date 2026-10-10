"""LangSmith tracing (no-op if disabled/not installed) + async sampled LLM-judge evals + own metrics store.
Privacy: LANGSMITH_HIDE_INPUTS / LANGSMITH_HIDE_OUTPUTS=true keeps tenant text out of traces.
"""
# import logging
# import os
# import random
# from datetime import datetime, timezone

# from bson import ObjectId

# from app.core.config import settings
# from app.services import mongodb_service as mdb

# from dotenv import load_dotenv
# load_dotenv()

# log = logging.getLogger("app.obs")

# try:
#     if os.getenv("LANGSMITH_TRACING", "false").lower() == "true":
#         from langsmith import traceable  # type: ignore
#     else:
#         raise ImportError
# except ImportError:
#     def traceable(*a, **k):  # no-op decorator
#         if len(a) == 1 and callable(a[0]) and not k:
#             return a[0]
#         return lambda f: f
import logging
import os
import random
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from bson import ObjectId

from app.core.config import settings
from app.services import mongodb_service as mdb

log = logging.getLogger("app.obs")
TRACING_ON = False
try:
    if os.getenv("LANGSMITH_TRACING", "false").lower() != "true":
        raise ImportError("LANGSMITH_TRACING is not 'true'")
    from langsmith import traceable  # type: ignore
    TRACING_ON = True
except ImportError as _e:
    _reason = str(_e)

    def traceable(*a, **k):  # no-op decorator
        if len(a) == 1 and callable(a[0]) and not k:
            return a[0]
        return lambda f: f

async def save_query_log(doc: dict) -> str:
    doc["ts"] = datetime.now(timezone.utc)
    res = await mdb.col("query_logs").insert_one(doc)
    return str(res.inserted_id)


async def run_sampled_eval(log_id: str, question: str, answer: str, contexts: list[str], force: bool = False) -> None:
    """LLM-as-judge OFF the request path, on a sample of traffic."""
    if not force and random.random() > settings.eval_sample_rate:
        return
    from app.services import gemini_service, template_manager
    try:
        prompt = template_manager.render("eval_judge.j2", question=question, answer=answer, contexts=contexts)
        s = await gemini_service.generate_json(prompt)
        scores = {k: max(0.0, min(1.0, float(s.get(k, 0)))) for k in
                  ("faithfulness", "context_relevancy", "answer_relevancy")}
        await mdb.col("query_logs").update_one({"_id": ObjectId(log_id)}, {"$set": {"eval": scores}})
        log.info("eval_done", extra={"log_id": log_id, **scores})
    except Exception:
        log.exception("eval_failed")


async def summary(org_id: str, days: int = 7) -> dict:
    since = datetime.now(timezone.utc).timestamp() - days * 86400
    since_dt = datetime.fromtimestamp(since, timezone.utc)
    pipe = [{"$match": {"org_id": org_id, "ts": {"$gte": since_dt}}},
            {"$group": {"_id": None, "queries": {"$sum": 1},
                        "avg_latency_ms": {"$avg": "$latency_ms"},
                        "avg_confidence": {"$avg": "$confidence"},
                        "abstained": {"$sum": {"$cond": ["$abstained", 1, 0]}},
                        "avg_faithfulness": {"$avg": "$eval.faithfulness"},
                        "avg_context_relevancy": {"$avg": "$eval.context_relevancy"},
                        "avg_answer_relevancy": {"$avg": "$eval.answer_relevancy"},
                        "avg_feedback": {"$avg": "$feedback.rating"}}}]
    rows = await mdb.col("query_logs").aggregate(pipe).to_list(1)
    out = rows[0] if rows else {"queries": 0}
    out.pop("_id", None)
    return out
