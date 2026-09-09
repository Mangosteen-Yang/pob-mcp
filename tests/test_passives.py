"""Passive-tree search + allocation tests."""

from __future__ import annotations

import pytest


def test_keystones_listed(fireball):
    ks = fireball.search_passives(node_type="Keystone", limit=5)["results"]
    assert ks
    assert all(n["type"] == "Keystone" for n in ks)


def test_alloc_then_dealloc(fireball):
    notables = fireball.search_passives(node_type="Notable", limit=400)["results"]
    reachable = [n for n in notables if n.get("pathDist")]
    assert reachable, "no reachable notables"
    target = min(reachable, key=lambda n: n["pathDist"])

    res = fireball.alloc_passive(target["id"])
    assert res["ok"] and res["pointsSpent"] >= 1
    assert res["statsDelta"]

    de = fireball.dealloc_passive(target["id"])
    assert de["ok"] and de["pointsFreed"] >= 1


def test_optimize_improves_dps(fireball):
    res = fireball.optimize_passives(metric="TotalDPS", points=5)
    assert res["allocated"], "optimizer allocated nothing"
    assert res["finalValue"] > res["startValue"]


def test_split_personality_jewel_opens_other_class_regions(engine):
    # "Split Personality" grants "allocate Passive Skills from <class>'s starting point" for several
    # classes. Socketing it must register in the tree (spec.jewels) AND rebuild paths so each
    # granted class-start becomes a path ROOT — making far regions cheaper for optimize_passives /
    # alloc_passive. Regression for the dynamic-jewel pathing fix (shim socket-sync + fork
    # multi-start). Uses classes that are NOT last in the list to prove ALL granted starts apply,
    # not just PoB's single (last-overwritten) jewelData.alternateClassStart.
    engine.new_build()
    engine.set_class("Sorceress")
    engine.set_level(90)
    engine.alloc_passive(2491)  # allocate a jewel socket so a socketed jewel actually applies

    def cheap_notables(k=20):
        nodes = engine.search_passives(node_type="Notable", limit=4000)["results"]
        return sum(1 for n in nodes if (n.get("pathDist") or 1000) <= k)

    before = cheap_notables()
    raw = (
        "Rarity: Unique\nSplit Personality\nRuby\nLimited to: 1\n"
        "Can Allocate Passive Skills from the Mercenary's starting point\n"
        "Can Allocate Passive Skills from the Ranger's starting point\n"
        "Can Allocate Passive Skills from the Warrior's starting point\n"
        "Can Allocate Passive Skills from the Shadow's starting point\nCorrupted"
    )
    engine.call("equip_jewel", socket=2491, raw=raw)
    after = cheap_notables()
    assert after > before + 25, f"jewel opened too few cheap notables: {before} -> {after}"

    # Unequipping reverts the tree pathing (no stale jewel state left behind).
    engine.call("unequip_item", slot="Jewel 2491")
    assert cheap_notables() == before


def test_set_class_and_ascendancy(engine):
    engine.new_build()
    r = engine.set_class("Mercenary", "Witchhunter")
    assert r["ok"] and r["class"] == "Mercenary" and r["ascendancy"] == "Witchhunter"

    # a Witchhunter ascendancy node should now exist and be reachable
    nodes = engine.search_passives(node_type=None, limit=6000)["results"]
    wh = [n for n in nodes if (n.get("ascendancy") or "") == "Witchhunter" and n.get("pathDist")]
    assert wh, "no reachable Witchhunter ascendancy nodes after set_class"

    # optimize now paths from the correct class start
    op = engine.optimize_passives(metric="Life", points=5)
    assert op["finalValue"] > op["startValue"]


def test_search_passives_ascendancy_and_partial(engine):
    engine.new_build()
    engine.set_class("Mercenary", "Witchhunter")
    # ascendancy name is now searchable -> returns that ascendancy's nodes
    asc = engine.search_passives(query="Witchhunter")["results"]
    assert asc and all(n.get("ascendancy") == "Witchhunter" for n in asc)
    # multi-word/conceptual query returns ranked partial matches (used to AND to []):
    multi = engine.search_passives(query="explode on death fire damage", limit=10)["results"]
    assert multi


def test_search_passives_reachable_first(fireball):
    # with no query, browse mode ranks reachable nodes (lowest pathDist) first
    res = fireball.search_passives(node_type="Notable", limit=50)["results"]
    dists = [n["pathDist"] for n in res if n.get("pathDist") is not None]
    assert dists == sorted(dists)


def test_set_class_unknown_lists_valid_options(engine):
    engine.new_build()
    bad_cls = engine.set_class("Notaclass")
    assert bad_cls["ok"] is False
    # the error must help the model self-correct by naming the real classes
    assert "Valid classes" in bad_cls["error"] and "Witch" in bad_cls["error"]

    bad_asc = engine.set_class("Witch", "Witchhunter")  # Witchhunter belongs to Mercenary
    assert bad_asc["ok"] is False
    assert "Valid ascendancies" in bad_asc["error"]


def test_set_level(engine):
    engine.new_build()
    base = engine.get_stats(["Life"])["stats"]["Life"]
    r = engine.set_level(90)
    assert r["ok"] and r["level"] == 90
    assert r["stats"]["Life"] > base  # higher level => more life (auto-leveling disabled)


def test_get_build_readback(engine):
    engine.new_build()
    engine.set_class("Mercenary", "Witchhunter")
    engine.set_level(90)
    engine.paste_skill("Detonate Living 20/0  1")
    b = engine.get_build()
    assert b["class"] == "Mercenary" and b["ascendancy"] == "Witchhunter" and b["level"] == 90
    assert b["mainSkill"] == "Detonate Living"


def test_list_config_options(engine):
    opts = engine.list_config_options(query="boss")["options"]
    assert any(o["var"] == "enemyIsBoss" for o in opts)


def test_equip_then_unequip(engine):
    engine.new_build()
    engine.paste_skill("Fireball 20/0  1")
    engine.add_item("Rarity: Rare\nR\nRuby Ring\n+50 to maximum Life")
    assert "Ring 1" in engine.get_build()["gear"]
    engine.unequip_item("Ring 1")
    assert "Ring 1" not in engine.get_build()["gear"]


def test_get_defenses(engine):
    engine.new_build()
    engine.set_class("Mercenary", "Witchhunter")
    engine.set_level(90)
    engine.paste_skill("Detonate Living 20/0  1")
    d = engine.get_defenses()
    assert d["life"] and d.get("note")
    assert set(d["resistances"]) == {"fire", "cold", "lightning", "chaos"}
    # The note must report the *actual* penalty (config default -60), not a hard-coded guess.
    # PoB nets that against a +10% elemental baseline, so a fresh elemental resist = penalty + 10.
    assert d["resistPenalty"] == -60
    assert d["resistances"]["fire"] == d["resistPenalty"] + 10


def test_points_available_scales_with_level(engine):
    engine.new_build()
    engine.set_level(90)
    a90 = engine.get_build()["pointsAvailable"]
    engine.set_level(20)
    a20 = engine.get_build()["pointsAvailable"]
    assert a90 > a20 > 0


def test_attack_skill_no_weapon_warning(engine):
    engine.new_build()
    engine.set_class("Monk", "Martial Artist")
    r = engine.paste_skill("Tempest Flurry 20/0  1")  # attack skill, no weapon
    assert r.get("warning") and "weapon" in r["warning"].lower()
    engine.add_item("Rarity: Rare\nX\nSteelpoint Quarterstaff\n120% increased Physical Damage")
    assert engine.get_stats(["TotalDPS"]).get("warning") is None  # cleared once armed


def test_optimize_balanced_raises_offense_and_defense(engine):
    engine.new_build()
    engine.set_class("Monk", "Martial Artist")
    engine.set_level(90)
    engine.paste_skill("Tempest Flurry 20/0  1")
    engine.add_item("Rarity: Rare\nX\nSteelpoint Quarterstaff\n120% increased Physical Damage")
    r = engine.optimize_passives(metric="balanced", points=12)
    assert r["finalDPS"] > r["startDPS"] and r["finalEHP"] > r["startEHP"]


def test_scaffold_gear_caps_resists_gap_driven(engine):
    from server import scaffold

    engine.new_build()
    engine.set_class("Monk", "Martial Artist")
    engine.set_level(90)
    engine.paste_skill("Tempest Flurry 20/0  1")
    engine.add_item("Rarity: Rare\nX\nSteelpoint Quarterstaff\n120% increased Physical Damage")
    before_life = engine.get_defenses()["life"]
    r = scaffold.scaffold_gear(engine)
    assert r["ok"] and r["filled"]
    ra = r["resistsAfter"]
    assert ra["fire"] >= 75 and ra["cold"] >= 75 and ra["lightning"] >= 75  # capped
    assert r["lifeAfter"] > before_life  # life pool added (auto)
    # pool="none" -> resists only, no life/ES
    engine.new_build()
    engine.set_class("Witch")
    r2 = scaffold.scaffold_gear(engine, pool="none")
    assert all(
        "Life" not in m and "Energy Shield" not in m for f in r2["filled"] for m in f["mods"]
    )


def test_scaffold_gear_es_default_for_int_class(engine):
    from server import scaffold

    # an intelligence class (Sorceress) auto-defaults to Energy Shield, not Life (#9)
    engine.new_build()
    engine.set_class("Sorceress", "Stormweaver")
    engine.set_level(90)
    engine.paste_skill("Spark 20/0  1")
    r = scaffold.scaffold_gear(engine)
    assert r["pool"] == "energy_shield"
    assert (r["energyShieldAfter"] or 0) > 0
    # chaos resistance isn't scaffolded -> surfaced + flagged in the note (#9)
    assert "chaosResAfter" in r
    if (r["chaosResAfter"] or 0) <= 0:
        assert "Chaos resistance" in r["note"]


def test_engine_reports_tree_version(engine):
    # the ready frame surfaces the passive-tree data version (used by engine_health)
    assert engine.info.get("treeVersion")


def test_engine_health_reports_versions():
    from server.main import engine_health

    h = engine_health()
    assert h["pong"] is True
    assert h["serverVersion"] and h["dataSource"] in {"bundled", "user-data"}
    assert h["treeVersion"]


def _tree_build(engine):
    """A real allocated tree to rank: Sorceress, Spark, an ascendancy notable + a small tree."""
    engine.new_build()
    engine.set_class("Sorceress", "Stormweaver")
    engine.set_level(60)
    engine.paste_skill("Spark 20/0  1")
    # One ascendancy notable, picked by reachability rather than id so a tree bump can't stale it —
    # the include_ascendancy filter needs an allocated ascendancy node to actually exclude.
    asc = [
        n
        for n in engine.search_passives(node_type="Notable", limit=2000)["results"]
        if n.get("ascendancy") and n.get("pathDist")
    ]
    assert asc, "no reachable ascendancy notable"
    engine.alloc_passive(min(asc, key=lambda n: n["pathDist"])["id"])
    engine.optimize_passives(metric="TotalDPS", points=20)
    return engine


def test_rank_contributions_ranks_allocated_nodes(engine):
    b = _tree_build(engine)
    base = b.get_stats(["TotalDPS"])["stats"]["TotalDPS"]
    r = b.rank_passive_contributions(metric="TotalDPS", limit=5)

    assert r["ok"] and r["results"]
    assert r["baseValue"] == pytest.approx(base, rel=1e-9)
    assert len(r["results"]) <= 5 and r["nodesTested"] >= len(r["results"])
    # sorted by contribution, descending
    deltas = [n["delta"] for n in r["results"]]
    assert deltas == sorted(deltas, reverse=True)
    # the top node on a DPS-optimized tree must actually carry damage
    assert deltas[0] > 0
    top = r["results"][0]
    assert top["name"] and top["id"] and top["type"]
    assert top["without"] == pytest.approx(base - top["delta"], rel=1e-9)
    assert top["deltaPct"] == pytest.approx(top["delta"] / base * 100, rel=1e-9)


def test_rank_contributions_matches_a_real_dealloc(engine):
    """The what-if delta must equal what removing the node really costs.

    Checked on a node whose removal frees exactly one point (no dependents), so the real
    dealloc removes the same single node the what-if simulated.
    """
    b = _tree_build(engine)
    # Restore by RELOADING, never by alloc_passive: alloc re-routes by shortest path, which can
    # take a different route than the one just removed and silently drift the tree between probes.
    xml = b.get_xml()
    base = b.get_stats(["TotalDPS"])["stats"]["TotalDPS"]
    ranked = b.rank_passive_contributions(metric="TotalDPS", limit=40)["results"]

    for node in ranked:
        if node["delta"] <= 0 or node.get("ascendancy"):
            continue
        b.load_build_xml(xml)
        d = b.dealloc_passive(node["id"])
        if not d.get("ok") or d["pointsFreed"] != 1:
            continue  # cascaded (or gone) — not the single-node case we're checking
        after = b.get_stats(["TotalDPS"])["stats"]["TotalDPS"]
        assert base - after == pytest.approx(node["delta"], rel=1e-6)
        return
    pytest.skip("no dependent-free contributing node on this tree")


def test_rank_contributions_filters_and_bad_metric(engine):
    b = _tree_build(engine)

    notables = b.rank_passive_contributions(metric="TotalDPS", limit=5, node_type="Notable")
    assert notables["ok"]
    assert all(n["type"] == "Notable" for n in notables["results"])

    no_asc = b.rank_passive_contributions(metric="TotalDPS", limit=50, include_ascendancy=False)
    assert all(not n.get("ascendancy") for n in no_asc["results"])
    # ascendancy nodes are allocated on this build, so excluding them tests strictly fewer nodes
    with_asc = b.rank_passive_contributions(metric="TotalDPS", limit=50)
    assert with_asc["nodesTested"] > no_asc["nodesTested"]

    bad = b.rank_passive_contributions(metric="NotARealStat")
    assert bad["ok"] is False and "NotARealStat" in bad["error"]


def test_rank_contributions_zero_metric_explains_itself(engine):
    # A metric the build has none of (Ward) makes every delta 0 — the ranking must say why rather
    # than hand back a page of silent zeros.
    b = _tree_build(engine)
    assert b.get_stats(["Ward"])["stats"]["Ward"] == 0
    r = b.rank_passive_contributions(metric="Ward")
    assert r["ok"] and r["baseValue"] == 0
    assert "note" in r and "0 on this build" in r["note"]


def test_rank_contributions_limit_zero_returns_all(engine):
    b = _tree_build(engine)
    r = b.rank_passive_contributions(metric="TotalDPS", limit=0)
    assert len(r["results"]) == r["nodesTested"] > 10
