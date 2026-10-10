"""ACL leak tests. Pure-logic tests run anywhere; extend with integration tests against a test Mongo/Pinecone."""
import itertools
from app.core.acl import acl_allows, build_pinecone_filter, mongo_acl_filter


def test_clearance_blocks():
    assert not acl_allows(2, ["engineering"], 4, ["all"])


def test_group_blocks():
    assert not acl_allows(4, ["engineering"], 1, ["hr"])


def test_all_group_is_company_wide():
    assert acl_allows(1, [], 1, ["all"])


def test_group_overlap_allows():
    assert acl_allows(2, ["devops"], 2, ["engineering", "devops"])


def test_empty_doc_groups_deny_everyone():
    assert not acl_allows(4, ["x"], 0, [])


def test_filters_always_include_clearance_and_all():
    f = build_pinecone_filter(2, ["a"])
    assert f["clearance_level"] == {"$lte": 2}
    assert set(f["allowed_groups"]["$in"]) == {"a", "all"}
    m = mongo_acl_filter("org_X", 2, ["a"])
    assert m["org_id"] == "org_X" and m["clearance_level"] == {"$lte": 2}


def test_filter_and_predicate_agree_exhaustively():
    """Pinecone/Mongo filter semantics must equal acl_allows for every combination."""
    for uc, dc in itertools.product(range(5), range(5)):
        for ug, dg in itertools.product([[], ["a"], ["b"]], [["all"], ["a"], ["a", "b"], []]):
            f = build_pinecone_filter(uc, ug)
            filt = dc <= f["clearance_level"]["$lte"] and bool(set(dg) & set(f["allowed_groups"]["$in"]))
            assert filt == acl_allows(uc, ug, dc, dg)

import pytest
from app.core.config import Settings


def test_retrieval_mode_accepts_three_values_case_insensitive():
    for m in ("hybrid", "dense", "sparse", " Sparse "):
        assert Settings(retrieval_mode=m).retrieval_mode == m.strip().lower()


def test_retrieval_mode_rejects_unknown_value():
    with pytest.raises(Exception):
        Settings(retrieval_mode="bogus")

def test_documents_filter_uses_classification_level():
    f = mongo_acl_filter("org_X", 2, ["a"], level_field="classification_level")
    assert "classification_level" in f and "clearance_level" not in f
