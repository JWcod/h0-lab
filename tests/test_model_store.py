"""M2:我的模型存取(private/models)與用模型重算報告分數。"""
from __future__ import annotations

import copy
import json
import math
import random

import pytest

from lab import config
from lab.analysis import scoring as SC
from lab.models import apply, schema, store
from tests.test_model_engine import _same, make_ctx


@pytest.fixture()
def priv(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PRIVATE_DIR", tmp_path)
    return tmp_path


def test_list_has_shipped_model(priv):
    ms = store.list_models()
    assert [m["id"] for m in ms if m["source"] == "shipped"] == ["jw-mf-2.0"]
    assert ms[0]["n_metrics"] == 66 and ms[0]["n_custom"] == 0


def test_fork_save_get_archive_roundtrip(priv):
    s = store.fork("jw-mf-2.0", "my-quality", "我的品質加重版")
    base = schema.load_shipped("jw-mf-2.0")
    assert s["score_hash"] == schema.score_hash(base)          # 只複製,分數定義相同
    m = store.get("my-quality")
    assert m["based_on"] == f"jw-mf-2.0@{schema.score_hash(base)}" and m["family_id"] == "my-quality"
    m["pillar_weights"]["long_term"]["quality"] = 40
    s2 = store.save(m)
    assert s2["score_hash"] != s["score_hash"]
    assert json.loads((priv / "models" / "my-quality.json").read_text(encoding="utf-8"))["pillar_weights"][
        "long_term"]["quality"] == 40
    store.archive("my-quality")
    assert not (priv / "models" / "my-quality.json").exists()
    assert len(list((priv / "models" / "_archive").glob("my-quality.*.json"))) == 1


@pytest.mark.parametrize("mid", ["jw-mf-2.0", "../evil", "Upper", "", "a" * 60, "x/y", "smoke\n", "a\nb"])
def test_save_rejects_bad_or_shipped_ids(priv, mid):
    m = schema.load_shipped("jw-mf-2.0") | {"id": mid}
    with pytest.raises(schema.ModelError):
        store.save(m)


def test_save_shipped_id_says_cannot_overwrite(priv):
    # M6 Verifier:出廠代號有句點,舊順序會先被格式檢查擋下,回錯的訊息
    m = schema.load_shipped("jw-mf-2.0")
    with pytest.raises(schema.ModelError, match="不能覆寫出廠模型"):
        store.save(m)
    assert not (priv / "models" / "jw-mf-2.0.json").exists()


def test_get_rejects_path_traversal(priv):
    for bad in ("../../etc/passwd", "smoke\n"):
        with pytest.raises(schema.ModelError):
            store.get(bad)


def test_validated_scores_match_report_policy():
    """long/short_term_validated 和報告的口徑相同(只用 validated_pillars)。"""
    ctx = make_ctx(random.Random(21))
    p = config.load_params()
    pillars = SC.build(copy.deepcopy(ctx))
    v = p["validated_pillars"]
    only = lambda w: {k: x for k, x in w.items() if k in v}  # noqa: E731
    out = apply.rescore(apply.json_safe(SC.base_vars(ctx)), schema.load_shipped("jw-mf-2.0"), "TW", v)
    assert out["short_term_validated"] == SC.composite(pillars, only(SC.short_term_weights(p, "TW")))
    assert out["long_term_validated"] == SC.composite(pillars, only(p["pillar_weights"]["long_term"]))


def test_broken_user_model_is_listed_with_error(priv):
    (priv / "models").mkdir()
    (priv / "models" / "broken.json").write_text("{", encoding="utf-8")
    assert any(m["id"] == "broken" and "error" in m for m in store.list_models())


def test_rescore_with_shipped_model_matches_report_pillars():
    """從基礎變數快照重算 = 報告原本的面向分數(沒有 NaN 時逐位元相同)。"""
    r = random.Random(7)
    m = schema.load_shipped("jw-mf-2.0")
    n = 0
    for _ in range(500):
        ctx = make_ctx(r)
        env = SC.base_vars(ctx)
        if any(isinstance(v, float) and math.isnan(v) for v in env.values()):
            continue
        out = apply.rescore(apply.json_safe(env), m, "TW")
        assert _same(out["pillars"], SC.build(copy.deepcopy(ctx)))
        n += 1
    assert n > 50


def test_rescore_output_has_no_rating_words():
    ctx = make_ctx(random.Random(3))
    out = apply.rescore(apply.json_safe(SC.base_vars(ctx)), schema.load_shipped("jw-mf-2.0"), "US",
                        ["quality", "valuation"])
    # 不出評等 / 訊號 / 動作欄位(指標名稱本身可能含「買進」,例如內部人買進,所以檢查欄位而不是字眼)
    assert set(out) == {"model", "pillars", "long_term", "short_term", "long_term_validated", "short_term_validated",
                        "unvalidated_pillars", "status", "disclaimer"}
    for k in ("long_term", "short_term", "long_term_validated", "short_term_validated"):
        assert set(out[k]) <= {"score", "grade", "used", "coverage", "weights"}
    assert out["status"] == "未驗證" and "flows" in out["unvalidated_pillars"]
    json.dumps(out, allow_nan=False)   # 可以直接當 API 回應


def test_report_with_model_param(priv):
    from fastapi import HTTPException

    from app import server
    env = apply.json_safe(SC.base_vars(make_ctx(random.Random(9))))
    r = {"meta": {"market": "TW"}, "model_env": env, "pillars": {}}
    # 沒指定或指定內建模型:回對外版本(lab/public.py,不含內建評等與面向分數),不重算
    for mid in (None, "jw-mf-2.0"):
        pub = server._with_model(r, mid)
        assert pub is not r and "pillars" not in pub and "custom_model" not in pub and "model_env" not in pub
    assert "pillars" in r, "不改快取裡的報告"
    store.fork("jw-mf-2.0", "mine-a", "我的")
    with pytest.raises(HTTPException) as e:                          # 沒改設定 = 內建模型:不給個股分數
        server._with_model(r, "mine-a")
    assert e.value.status_code == 400
    m = store.get("mine-a")
    m["pillar_weights"]["long_term"] = {"quality": 100}
    store.save(m)
    out = server._with_model(r, "mine-a")
    assert "custom_model" not in r                                   # 不改快取裡的報告
    assert out["custom_model"]["long_term"]["used"] == ["quality"]
    assert out["custom_model"]["long_term"]["score"] == out["custom_model"]["pillars"]["quality"]["score"]
    with pytest.raises(HTTPException) as e:
        server._with_model(r, "nope")
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        server._with_model({"meta": {"market": "TW"}}, "mine-a")
    assert e.value.status_code == 409


def test_score_hash_ignores_json_number_format():
    """瀏覽器送回的 JSON 會把 0.0 變成 0;分數定義沒變,雜湊不能變。"""
    m = schema.load_shipped("jw-mf-2.0")
    roundtrip = json.loads(json.dumps(m).replace(".0,", ",").replace(".0}", "}"))
    assert schema.score_hash(roundtrip) == schema.score_hash(m)
    m2 = copy.deepcopy(m)
    m2["metrics"][0]["score"]["good"] = 0.26
    assert schema.score_hash(m2) != schema.score_hash(m)
