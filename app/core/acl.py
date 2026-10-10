"""Pure access-control logic (no I/O) so it can be unit tested exhaustively.

Rule: clearance_level <= user.clearance AND (allowed_groups overlaps user_groups OR "all" in allowed_groups)
"""
ALL = "all"


def _groups(user_groups) -> list[str]:
    return sorted(set(user_groups or []) | {ALL})


def build_pinecone_filter(clearance: int, groups: list[str]) -> dict:
    return {"clearance_level": {"$lte": clearance}, "allowed_groups": {"$in": _groups(groups)}}


# def mongo_acl_filter(org_id: str, clearance: int, groups: list[str]) -> dict:
#     return {
#         "org_id": org_id,
#         "clearance_level": {"$lte": clearance},
#         "allowed_groups": {"$in": _groups(groups)},
#     }
def mongo_acl_filter(org_id: str, clearance: int, groups: list[str],
                     level_field: str = "clearance_level") -> dict:
    """chunks use 'clearance_level'; the documents collection uses 'classification_level'."""
    return {
        "org_id": org_id,
        level_field: {"$lte": clearance},
        "allowed_groups": {"$in": _groups(groups)},
    }


def acl_allows(user_clearance: int, user_groups: list[str], doc_clearance: int, doc_groups: list[str]) -> bool:
    if doc_clearance > user_clearance:
        return False
    return bool(set(doc_groups or []) & set(_groups(user_groups)))


def atlas_search_pipeline(index: str, query: str, org_id: str, clearance: int,
                          groups: list[str], limit: int) -> list[dict]:
    """BM25 query with the SAME ACL rule enforced inside the search itself."""
    return [
        {"$search": {"index": index, "compound": {
            "must": [{"text": {"query": query, "path": ["text", "title"]}}],
            "filter": [
                {"equals": {"path": "org_id", "value": org_id}},
                {"range": {"path": "clearance_level", "lte": clearance}},
                {"in": {"path": "allowed_groups", "value": _groups(groups)}},
            ]}}},
        {"$limit": limit},
        {"$project": {"title": 1, "text": 1, "doc_id": 1, "location": 1, "org_id": 1,
                      "clearance_level": 1, "allowed_groups": 1,
                      "bm25_score": {"$meta": "searchScore"}}},
    ]