"""Exact adaptive CP-SAT search with the original power/score/reward oracle.

No candidate truncation or solve deadline. Small pools use the matching engine.
AP starts with a conservative relaxation and adds conditional exact facts until
the optimum is proved and agrees with the unaltered 120-order reference.
Only completed objectives, oracle facts and incumbent hints are resumable;
native search trees are not serialized or represented as completed coverage.
"""
from collections import Counter
from copy import deepcopy
import threading
import time
import math

import planner_core as p
from score_bounds import pool_upper_signature, power_thresholds, signature_score
from search_cache import digest

ENGINE_SCHEMA = 2
MODES = ("normal", "challenge")
OBJECTIVES = ("event_pt", "shop_pt")
SAFE_INTEGER = (1 << 61) - 1


class UnsupportedModel(Exception):
    """Use the existing complete engine for arithmetic not yet linearized."""


def should_use_solver(member_teams, snap_count, specs):
    # Dispatch choices, never limits: neither engine discards input candidates.
    ap = any(spec["method"] == "ap" for sheets in specs.values() for spec in sheets)
    return member_teams > 64 or (ap and member_teams * math.perm(snap_count, 5) > 5000)


def _integer_weight(vector):
    value = sum(vector)
    if value < 0 or value % 10000:
        raise UnsupportedModel("nonintegral or negative power terms")
    return value // 10000


def _leader_matrix(data, model):
    result = {}
    for leader in model.mids:
        card, own = model.cards[leader], model.own[leader]
        effects = [e for e in data.tables["MasterLeaderSkillEffect"]
                   if e["_leaderSkillID"] == card["_leaderSkillID"] and e["_level"] == own["leader_skill_level"]]
        if not effects:
            raise UnsupportedModel("missing leader effects")
        for e in effects:
            if e["_skillEffectType"] == 0:
                continue
            if (e["_skillEffectType"] not in (1000, 1001, 1002, 1003) or e["_skillConditionGroup"]
                    or e["_skillCumulativeConditionID"] or e["_effectExecuteLimitCount"]
                    or e["_effectExecuteLimitResetConditionGroup"]):
                raise UnsupportedModel("formation-dependent leader")
        for member in model.mids:
            rates = p.leader_rates(data, card, own, [card] + [model.cards[member]] * 4,
                                  [model.chars[leader]] + [model.chars[member]] * 4, model.music_type)[1]
            result[leader, member] = _integer_weight(p.dp._mul_floor(model.b[member], rates))
    return result


class Search:
    enable_seeds = True
    def __init__(self, request, data, model, specs, member_teams, progress, cancelled, cache, run_id, begin):
        try:
            from ortools.sat.python import cp_model
        except ImportError:
            raise p.InputError("源码版缺少新算法依赖。请按使用说明安装 requirements.txt，或使用已包含依赖的 Windows 便携版。") from None
        self.cp = cp_model
        self.request, self.data, self.model, self.specs = request, data, model, specs
        self.member_teams, self.progress, self.cancelled, self.cache = member_teams, progress, cancelled, cache
        self.run_id, self.begin = run_id, begin
        self.settings = request["settings"]
        self.mids, self.sids = tuple(model.mids), tuple(model.sids)
        self.prepared_keys, self.step_keys, self.oracle_data = [], [], {}
        self.prepared, self.cached_sheets, self.completed, self.cached_steps = 0, 0, 0, 0
        self.total_sheets = sum(map(len, specs.values()))
        self.total_steps = sum(1 + sum(min(3, len({s["song_id"] for s in specs[m]})) - 1 for m in MODES)
                               for _ in OBJECTIVES)
        self.stats, self.hint = Counter(), None
        self.metrics = getattr(model, "metrics", p.Trace(False))
        self.fact_index = {}
        self.seed_plans = []
        self.stage, self.phase = "准备新算法", ""
        self.run_key = "solver-run:" + digest({"schema": ENGINE_SCHEMA, "model": data.fingerprint(),
                                               "profile": request["profile"], "members": self.mids,
                                               "snaps": self.sids, "settings": self.settings, "specs": self.specs,
                                               "playability": request.get("playability", {})})
        if cache:
            previous = cache.get(self.run_key)
            if previous:
                self.hint = previous.get("hint")
        self.dirty = set()

    def check(self):
        if self.cancelled():
            raise p.Cancelled()

    def emit(self, **changes):
        self.progress(strategy="solver", stage=self.stage, phase=self.phase,
                      done=self.completed, total=self.total_steps, completed_steps=self.completed,
                      total_steps=self.total_steps, cached_steps=self.cached_steps,
                      completed_sheets=self.prepared, total_sheets=self.total_sheets,
                      cached_sheets=self.cached_sheets, elapsed_seconds=round(time.monotonic()-self.begin, 1),
                      **changes)

    def save(self, status="running"):
        if not self.cache:
            return
        for key in list(self.dirty):
            self.cache.put("solver-oracle:" + key, [], 0, facts=self.oracle_data[key])
            self.dirty.discard(key)
        self.cache.put(self.run_key, [], 0, hint=self.hint)
        self.cache.checkpoint(self.request, job_id=self.run_id, status=status, strategy="solver",
                              keys=self.prepared_keys, step_keys=self.step_keys, total_steps=self.total_steps,
                              completed_steps=self.completed, total_sheets=self.total_sheets,
                              completed_sheets=self.prepared)

    def prepare_pairs(self):
        self.pairs = {}
        if not any(s["method"] == "ap" for sheets in self.specs.values() for s in sheets):
            return
        chars = {m: self.model.cards[m]["_characterID"] for m in self.mids}
        self.stage = "核对所有候选卡的 AP 技能"
        self.emit()
        for member in self.mids:
            selected, used = [member], {chars[member]}
            for other in self.mids:
                if chars[other] not in used:
                    selected.append(other)
                    used.add(chars[other])
                if len(selected) == 5:
                    break
            for offset in range(len(self.sids)):
                self.check()
                binding = [self.sids[(offset+i) % len(self.sids)] for i in range(5)]
                if all((m, s) in self.pairs for m, s in zip(selected, binding)):
                    continue
                try:
                    contract = p.sk.derive_ap_skill_contract(self.data.snapshot, self.request["profile"], selected, binding,
                                                             _verified_inputs=self.data.skill_inputs)
                except ValueError as e:
                    raise p.InputError(f"当前 AP 模型尚未覆盖这组技能：{e}。可选择跳过参考，或调整候选卡。不会把该技能当作零效果。") from e
                for slot in contract["slots"]:
                    self.pairs[slot["member_id"], slot["snap_id"]] = {
                        "extension_ms": slot["extension_ms"], "active_live_effects": slot["active_live_effects"]}
        if len(self.pairs) != len(self.mids) * len(self.sids):
            raise RuntimeError("Incomplete AP pair preflight")

    def add_phase(self, mode):
        cm, model = self.base, self.model
        # Native pybind requires a concrete sequence; the browser also accepts it.
        linear_sum = lambda values: self.cp.LinearExpr.sum(list(values))
        prefix, challenge = mode + "_", mode == "challenge"
        sheets = self.specs[mode]
        model.music(sheets[0]["song_id"], challenge)
        leaders = _leader_matrix(self.data, model)
        pair = {(m, s): _integer_weight(model.pair[m, s]) for m in self.mids for s in self.sids}
        statics = []
        for spec in sheets:
            self.check()
            model.music(spec["song_id"], challenge)
            statics.append({m: _integer_weight(model.static[m]) for m in self.mids})
        upper = sum(sorted((max(row[m] for row in statics) + max(pair[m, s] for s in self.sids)
                            + max(leaders[l, m] for l in self.mids) for m in self.mids), reverse=True)[:5])
        x = {m: cm.new_bool_var(prefix + "m_" + str(m)) for m in self.mids}
        y = {s: cm.new_bool_var(prefix + "s_" + str(s)) for s in self.sids}
        z = {(m, s): cm.new_bool_var(prefix + f"bind_{m}_{s}") for m in self.mids for s in self.sids}
        leader = {m: cm.new_bool_var(prefix + "lead_" + str(m)) for m in self.mids}
        sheet_flags = [cm.new_bool_var(prefix + "sheet_" + str(i)) for i in range(len(sheets))]
        cm.add(sum(x.values()) == 5)
        cm.add(sum(y.values()) == 5)
        cm.add(sum(leader.values()) == 1)
        cm.add(sum(sheet_flags) == 1)
        sheet_index = cm.new_int_var(0, len(sheets)-1, prefix + "sheet_index")
        cm.add(sheet_index == sum(i * flag for i, flag in enumerate(sheet_flags)))
        song = cm.new_int_var(min(s["song_id"] for s in sheets), max(s["song_id"] for s in sheets), prefix + "song")
        cm.add(song == sum(s["song_id"] * flag for s, flag in zip(sheets, sheet_flags)))
        for m in self.mids:
            cm.add(sum(z[m, s] for s in self.sids) == x[m])
            cm.add(leader[m] <= x[m])
        for s in self.sids:
            cm.add(sum(z[m, s] for m in self.mids) == y[s])
        chars = {}
        for m in self.mids:
            chars.setdefault(model.cards[m]["_characterID"], []).append(m)
        for group in chars.values():
            cm.add(sum(x[m] for m in group) <= 1)
        lead_index = cm.new_int_var(0, len(self.mids)-1, prefix + "lead_index")
        cm.add(lead_index == sum(i * leader[m] for i, m in enumerate(self.mids)))
        parts = []
        for m in self.mids:
            for label, selector, values in (("static", sheet_index, [row[m] for row in statics]),
                                             ("leader", lead_index, [leaders[l, m] for l in self.mids])):
                value = cm.new_int_var(min(values), max(values), prefix + label + str(m))
                cm.add_element(selector, values, value)
                part = cm.new_int_var(0, max(values), prefix + "selected_" + label + str(m))
                cm.add_multiplication_equality(part, [value, x[m]])
                parts.append(part)
        power = cm.new_int_var(1, upper, prefix + "power")
        cm.add(power == linear_sum(parts) + linear_sum(pair[m, s] * z[m, s] for m in self.mids for s in self.sids))
        bonuses, bonus_bounds = {}, {}
        for name in OBJECTIVES:
            key = name + "_bonus_10000"
            bonus_bounds[name] = sum(sorted((model.mb[m][key] for m in self.mids), reverse=True)[:5]) + sum(sorted((model.sb[s][key] for s in self.sids), reverse=True)[:5])
            bonuses[name] = cm.new_int_var(0, bonus_bounds[name], prefix + name + "_bonus")
            cm.add(bonuses[name] == linear_sum(model.mb[m][key] * x[m] for m in self.mids)
                   + linear_sum(model.sb[s][key] * y[s] for s in self.sids))
        rank_flags = [cm.new_bool_var(prefix + "rank_" + rank) for rank in p.RANKS]
        cm.add(sum(rank_flags) == 1)
        scorers, sheet_keys = [], []
        options = list({p.Scores.skill_key([slot])[0]: slot for slot in self.pairs.values()}.values())
        for i, spec in enumerate(sheets):
            self.check()
            scorer = p.Scores(self.data, spec, challenge, self.metrics)
            scorers.append(scorer)
            key = p._sheet_cache_key(self.data, self.request["profile"], list(self.mids), list(self.sids), spec, mode)
            sheet_keys.append(key)
            prep_key = "solver-sheet:" + key
            saved = self.cache.get(prep_key) if self.cache else None
            if saved and saved.get("upper_power") == upper:
                thresholds = saved["thresholds"]
                self.cached_sheets += 1
            else:
                if spec["method"] == "skip":
                    score = scorer.skip_score
                else:
                    try:
                        signature = pool_upper_signature(scorer.chart, [options] * 5)
                    except ValueError as e:
                        raise UnsupportedModel(str(e)) from e
                    score = lambda value, c=scorer.chart, sig=signature: signature_score(c, sig, value)
                with self.metrics.measure("rank_thresholds"):
                    thresholds = power_thresholds(scorer.chart, score, upper)
                if self.cache:
                    self.cache.put(prep_key, [], 0, thresholds=thresholds, upper_power=upper)
            for r, flag in enumerate(rank_flags):
                cm.add(power >= thresholds[r]).only_enforce_if([sheet_flags[i], flag])
                if spec["method"] == "skip" and r+1 < len(p.RANKS):
                    cm.add(power < thresholds[r+1]).only_enforce_if([sheet_flags[i], flag])
            facts = self.cache.get("solver-oracle:" + key) if self.cache else None
            self.oracle_data[key] = facts.get("facts", []) if facts else []
            self.fact_index[key] = {}
            for fact in self.oracle_data[key]:
                identity = self.fact_identity(fact)
                previous = self.fact_index[key].get(identity)
                if previous is not None and previous != fact:
                    raise p.InputError("续算事实存在冲突，请清除计算缓存后重算。")
                self.fact_index[key][identity] = fact
            self.prepared_keys.append(prep_key)
            self.prepared += 1
            self.stage = f"准备谱面 {self.prepared} / {self.total_sheets}"
            self.emit()
            self.save()
        consumed = self.settings["challenge_cp" if challenge else "boost_per_live"]
        event = self.data.event
        bases = event["challenge_base_rewards" if challenge else "ordinary_base_rewards"]
        rates = next(r for r in event["challenge_cp_consumption_rows_raw" if challenge else "ordinary_boost_rows_raw"]
                     if r["_consumedChallengePointCount" if challenge else "_consumedLiveBoostCount"] == consumed)
        preview = p.rw.preview_challenge_rewards if challenge else p.rw.preview_normal_rewards
        try:
            gain_bounds = {name: max(preview(self.data.snapshot, rank, consumed, bonus_bounds["event_pt"], bonus_bounds["shop_pt"])["gained"][name]
                                     for rank in p.RANKS) for name in OBJECTIVES}
        except ValueError as e:
            raise UnsupportedModel("reward range cannot be conservatively linearized") from e
        cp_upper = 0 if challenge else max(b["cp_base"] * rates["_eventPointRate"] for b in bases)
        gained = {name: cm.new_int_var(0, gain_bounds[name], prefix + name) for name in OBJECTIVES}
        gained["cp"] = cm.new_int_var(0, cp_upper, prefix + "cp")
        for flag, rank in zip(rank_flags, p.RANKS):
            base = next(b for b in bases if b["rank"] == rank)
            value = cm.new_int_var(0, gain_bounds["event_pt"], prefix + "ep_" + rank)
            cm.add_division_equality(value, base["event_pt_base"] * rates["_eventPointRate"] * (10000 + bonuses["event_pt"]), 10000)
            cm.add(gained["event_pt"] == value).only_enforce_if(flag)
            shop_parts = []
            for j, reward in enumerate(base["shop_reward_rows"]):
                value = cm.new_int_var(0, gain_bounds["shop_pt"], prefix + f"sp_{rank}_{j}")
                cm.add_division_equality(value, reward["_resourceCount"] * rates["_liveMusicRewardRate"] * (10000 + bonuses["shop_pt"]), 10000)
                shop_parts.append(value)
            cm.add(gained["shop_pt"] == sum(shop_parts)).only_enforce_if(flag)
            cm.add(gained["cp"] == (0 if challenge else base["cp_base"] * rates["_eventPointRate"])).only_enforce_if(flag)
        return {"x": x, "y": y, "z": z, "leader": leader, "sheet_flags": sheet_flags,
                "sheet_index": sheet_index, "song": song, "power": power, "upper_power": upper,
                "bonus": bonuses, "gain": gained, "gain_bounds": gain_bounds, "cp_upper": cp_upper,
                "rank_flags": rank_flags, "scorers": scorers, "keys": sheet_keys, "consumed": consumed}

    def build(self):
        with self.metrics.measure("skill_preparation"):
            self.prepare_pairs()
        self.base = self.cp.CpModel()
        with self.metrics.measure("phase_model_preparation"):
            self.zones = {mode: self.add_phase(mode) for mode in MODES}
        normal, challenge = (self.zones[m] for m in MODES)
        plays = self.settings["boost_budget"] // self.settings["boost_per_live"]
        cost, starting = self.settings["challenge_cp"], self.settings["starting_cp"]
        cp_total = starting + plays * normal["gain"]["cp"]
        count_upper = (starting + plays * normal["cp_upper"]) // cost
        count = self.base.new_int_var(0, count_upper, "challenge_plays")
        self.base.add_division_equality(count, cp_total, cost)
        self.remaining = self.base.new_int_var(0, cost-1, "remaining_cp")
        self.base.add(self.remaining == cp_total - count * cost)
        self.totals, self.bounds = {}, {}
        for name in OBJECTIVES:
            upper = count_upper * challenge["gain_bounds"][name]
            part = self.base.new_int_var(0, upper, "challenge_total_" + name)
            self.base.add_multiplication_equality(part, [count, challenge["gain"][name]])
            self.bounds[name] = plays * normal["gain_bounds"][name] + upper
            self.totals[name] = self.base.new_int_var(0, self.bounds[name], "total_" + name)
            self.base.add(self.totals[name] == plays * normal["gain"][name] + part)
        for mode, zone in self.zones.items():
            for i, key in enumerate(zone["keys"]):
                for fact in self.oracle_data[key]:
                    self.apply_fact(self.base, zone, i, fact)
        error = self.base.validate()
        if error:
            raise UnsupportedModel(error)
        if self.enable_seeds and len(self.mids) > 10:
            with self.metrics.measure("verified_seeds"):
                self.prepare_seeds()

    def exact_row(self, mode, i, members, snaps):
        zone, spec = self.zones[mode], self.specs[mode][i]
        scorer = zone["scorers"][i]
        self.model.music(spec["song_id"], mode == "challenge")
        leader, vectors = self.model.best_leader(members)
        power = self.model.total(members, snaps, vectors)
        slots = [self.pairs[m, s] for m, s in zip(members, snaps)] if scorer.method == "ap" else None
        estimate = scorer.evaluate(power, slots)
        self.stats["score_evaluations"] += 1
        bonuses = self.model.bonuses(members, snaps)
        preview = p.rw.preview_challenge_rewards if mode == "challenge" else p.rw.preview_normal_rewards
        gains = preview(self.data.snapshot, estimate["rank"], zone["consumed"], bonuses["event_pt"], bonuses["shop_pt"],
                        _context=self.data.reward_context)["gained"]
        return {"member_ids": list(members), "snap_ids": list(snaps), "leader_member_id": leader,
                "song_id": spec["song_id"], "difficulty": spec["difficulty"], "power": power,
                "score": estimate, "bonuses_10000": bonuses, "per_live": gains}

    def prepare_seeds(self):
        """Three legal directions improve incumbents; every candidate stays searchable."""
        rows = {}
        for mode in MODES:
            self.model.music(self.specs[mode][0]["song_id"], mode == "challenge")
            rows[mode] = []
            for direction in ("power", *OBJECTIVES):
                self.check()
                def member_key(member):
                    power = sum(self.model.static[member]) + max(sum(self.model.pair[member, s]) for s in self.sids)
                    primary = power if direction == "power" else self.model.mb[member][direction + "_bonus_10000"]
                    return primary, power, -member
                members, characters = [], set()
                for member in sorted(self.mids, key=member_key, reverse=True):
                    character = self.model.cards[member]["_characterID"]
                    if character not in characters:
                        members.append(member)
                        characters.add(character)
                    if len(members) == 5:
                        break
                members.sort()
                available, snaps = set(self.sids), []
                for member in members:
                    def snap_key(snap):
                        power = sum(self.model.pair[member, snap])
                        primary = power if direction == "power" else self.model.sb[snap][direction + "_bonus_10000"]
                        return primary, power, -snap
                    snap = max(available, key=snap_key)
                    snaps.append(snap)
                    available.remove(snap)
                rows[mode].append(self.exact_row(mode, 0, tuple(members), tuple(snaps)))
        for normal in rows["normal"]:
            for challenge in rows["challenge"]:
                totals = p.cycle(normal["per_live"], challenge["per_live"], self.settings["boost_budget"],
                                 self.settings["boost_per_live"], self.settings["starting_cp"], self.settings["challenge_cp"])
                self.seed_plans.append({"normal": normal, "challenge": challenge, "totals": totals})
        self.metrics.count("validated_seed_plans", len(self.seed_plans))
        if not self.hint:
            best = max(self.seed_plans, key=lambda plan: (plan["totals"]["event_pt"], plan["totals"]["shop_pt"]))
            self.hint = {mode: {"sheet": 0, "members": best[mode]["member_ids"],
                               "bindings": list(zip(best[mode]["member_ids"], best[mode]["snap_ids"])),
                               "leader": best[mode]["leader_member_id"]} for mode in MODES}

    @staticmethod
    def apply_fact(cm, zone, i, fact):
        flags = [zone["sheet_flags"][i]]
        if fact["kind"] == "formation":
            flags += [zone["x"][m] for m in fact["members"]]
            cm.add(zone["leader"][fact["leader"]] == 1).only_enforce_if(flags)
            for rank_flag, threshold in zip(zone["rank_flags"], fact.get("thresholds", [])):
                cm.add(zone["power"] >= threshold).only_enforce_if(flags + [rank_flag])
        else:
            flags += [zone["z"][m, s] for m, s in zip(fact["members"], fact["snaps"])]
            cm.add(zone["rank_flags"][fact["rank"]] == 1).only_enforce_if(flags)
            cm.add(zone["power"] == fact["power"]).only_enforce_if(flags)

    def add_fact(self, cm, mode, i, fact):
        zone = self.zones[mode]
        key = zone["keys"][i]
        identity = self.fact_identity(fact)
        previous = self.fact_index[key].get(identity)
        if previous is not None:
            if previous != fact:
                raise RuntimeError("Conflicting exact oracle facts")
            return
        self.fact_index[key][identity] = fact
        self.oracle_data[key].append(fact)
        self.dirty.add(key)
        self.apply_fact(self.base, zone, i, fact)
        self.apply_fact(cm, zone, i, fact)

    @staticmethod
    def fact_identity(fact):
        return (fact["kind"], tuple(fact["members"]), tuple(fact.get("snaps", ())))

    def selected_hint(self, solver):
        return {mode: {"sheet": solver.value(zone["sheet_index"]),
                       "members": [m for m in self.mids if solver.value(zone["x"][m])],
                       "bindings": [[m, s] for (m, s), flag in zone["z"].items() if solver.value(flag)],
                       "leader": next(m for m in self.mids if solver.value(zone["leader"][m]))}
                for mode, zone in self.zones.items()}

    def add_hint(self, cm):
        cm.clear_hints()
        if not self.hint:
            return
        for mode, zone in self.zones.items():
            hint = self.hint[mode]
            members, bindings = set(hint["members"]), {tuple(b) for b in hint["bindings"]}
            snaps = {s for _, s in bindings}
            for m, flag in zone["x"].items():
                cm.add_hint(flag, int(m in members))
            for s, flag in zone["y"].items():
                cm.add_hint(flag, int(s in snaps))
            for pair, flag in zone["z"].items():
                cm.add_hint(flag, int(pair in bindings))
            for m, flag in zone["leader"].items():
                cm.add_hint(flag, int(m == hint["leader"]))
            for i, flag in enumerate(zone["sheet_flags"]):
                cm.add_hint(flag, int(i == hint["sheet"]))

    def native_solve(self, cm):
        self.check()
        self.add_hint(cm)
        solver = self.cp.CpSolver()
        solver.parameters.num_search_workers = 4
        solver.parameters.random_seed = 19471
        stopped, owner = threading.Event(), self
        lock, state = threading.Lock(), {"feasible": False, "hint": self.hint, "last": 0}

        class Callback(self.cp.CpSolverSolutionCallback):
            def on_solution_callback(callback):
                now = time.monotonic()
                if now - state["last"] >= 2:
                    hint = owner.selected_hint(callback)
                    with lock:
                        state.update(feasible=True, hint=hint, last=now)
                if owner.cancelled():
                    callback.stop_search()

        def monitor():
            last_save = time.monotonic()
            while not stopped.wait(.5):
                if self.cancelled():
                    solver.stop_search()
                with lock:
                    self.hint = state["hint"]
                    feasible = state["feasible"]
                self.emit(has_feasible_plan=feasible)
                if time.monotonic() - last_save >= 2:
                    self.save()
                    last_save = time.monotonic()

        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            solve_begin = time.perf_counter()
            with self.metrics.measure("solver"):
                status = solver.solve(cm, Callback())
            self.metrics.record_solver(self.solve_context, status=int(status),
                                       seconds=time.perf_counter() - solve_begin)
        finally:
            stopped.set()
            watcher.join()
        with lock:
            self.hint = state["hint"]
        self.check()
        self.stats["solver_calls"] += 1
        if status != self.cp.OPTIMAL:
            # No deadline is set. UNKNOWN/FEASIBLE must never become an optimum.
            raise p.InputError("新算法未能完成最优证明，已有进度会保留。可恢复输入继续计算。")
        self.hint = self.selected_hint(solver)
        return solver

    def oracle(self, cm, solver):
        rows, agrees = {}, True
        for mode, zone in self.zones.items():
            self.check()
            i = solver.value(zone["sheet_index"])
            spec, scorer, key = self.specs[mode][i], zone["scorers"][i], zone["keys"][i]
            members = tuple(m for m in self.mids if solver.value(zone["x"][m]))
            snaps = tuple(next(s for s in self.sids if solver.value(zone["z"][m, s])) for m in members)
            self.model.music(spec["song_id"], mode == "challenge")
            leader, vectors = self.model.best_leader(members)
            power = self.model.total(members, snaps, vectors)
            slots = [self.pairs[m, s] for m, s in zip(members, snaps)] if scorer.method == "ap" else None
            estimate = scorer.evaluate(power, slots)
            self.stats["score_evaluations"] += 1
            bonuses = self.model.bonuses(members, snaps)
            preview = p.rw.preview_challenge_rewards if mode == "challenge" else p.rw.preview_normal_rewards
            gains = preview(self.data.snapshot, estimate["rank"], zone["consumed"], bonuses["event_pt"], bonuses["shop_pt"],
                            _context=self.data.reward_context)["gained"]
            rows[mode] = {"member_ids": list(members), "snap_ids": list(snaps), "leader_member_id": leader,
                          "song_id": spec["song_id"], "difficulty": spec["difficulty"], "power": power,
                          "score": estimate, "bonuses_10000": bonuses, "per_live": gains}
            facts = self.oracle_data[key]
            if ("formation", members, ()) not in self.fact_index[key]:
                fact = {"kind": "formation", "members": list(members), "leader": leader}
                if scorer.method == "ap":
                    signature = pool_upper_signature(scorer.chart, [[self.pairs[m, s] for s in self.sids] for m in members])
                    fact["thresholds"] = power_thresholds(scorer.chart, lambda value: signature_score(scorer.chart, signature, value), zone["upper_power"])
                self.add_fact(cm, mode, i, fact)
                self.stats["formation_bounds"] += 1
            rank = p.RANKS.index(estimate["rank"])
            projected = next(r for r, flag in enumerate(zone["rank_flags"]) if solver.value(flag))
            if scorer.method == "ap" and ("binding", members, snaps) not in self.fact_index[key]:
                self.add_fact(cm, mode, i, {"kind": "binding", "members": list(members), "snaps": list(snaps), "rank": rank, "power": power})
                self.stats["exact_binding_facts"] += 1
            if projected != rank or solver.value(zone["power"]) != power or not solver.value(zone["leader"][leader]):
                agrees = False
            elif gains != {name: solver.value(zone["gain"][name]) for name in ("cp", *OBJECTIVES)}:
                raise RuntimeError("Reward model disagrees with reference oracle")
        totals = p.cycle(rows["normal"]["per_live"], rows["challenge"]["per_live"], self.settings["boost_budget"],
                         self.settings["boost_per_live"], self.settings["starting_cp"], self.settings["challenge_cp"])
        if agrees and any(totals[name] != solver.value(self.totals[name]) for name in OBJECTIVES):
            raise RuntimeError("Resource cycle disagrees with reference oracle")
        return {**rows, "totals": totals}, agrees

    def groups(self, objective):
        other = "shop_pt" if objective == "event_pt" else "event_pt"
        normal, challenge = (self.zones[m] for m in MODES)
        groups = []
        def append(parts):
            expression, bound = 0, 0
            variables = []
            for variable, upper in parts:
                if bound * (upper + 1) + upper > SAFE_INTEGER:
                    groups.append((expression, variables))
                    expression, bound, variables = 0, 0, []
                expression = expression * (upper + 1) + variable
                bound = bound * (upper + 1) + upper
                variables.append(variable)
            groups.append((expression, variables))
        append([(self.totals[objective], self.bounds[objective]), (self.totals[other], self.bounds[other])])
        append([(self.remaining, self.settings["challenge_cp"]-1),
                (normal["power"], normal["upper_power"]), (challenge["power"], challenge["upper_power"])])
        # Constant song IDs need no extra search. Otherwise combine their final
        # tie breaks without losing integer precision or changing precedence.
        songs = []
        for mode, zone in self.zones.items():
            ids = [s["song_id"] for s in self.specs[mode]]
            if max(ids) != min(ids):
                songs.append((max(ids) - zone["song"], max(ids) - min(ids)))
        if songs:
            append(songs)
        return groups

    def solve_step(self, objective, mode=None, excluded=()):
        key = "solver-step:" + digest({"run": self.run_key, "objective": objective, "mode": mode, "excluded": list(excluded)})
        saved = self.cache.get(key) if self.cache else None
        if saved and saved.get("proven"):
            self.completed += 1
            self.cached_steps += 1
            self.step_keys.append(key)
            self.emit()
            return deepcopy(saved["plan"])
        cm = self.base.clone()
        if mode:
            for sid in excluded:
                cm.add(self.zones[mode]["song"] != sid)
        eligible = [plan for plan in self.seed_plans if mode is None or plan[mode]["song_id"] not in excluded]
        if eligible:
            cm.add(self.totals[objective] >= max(plan["totals"][objective] for plan in eligible))
        self.stage = ("活动 PT" if objective == "event_pt" else "商店 PT") + " · " + (
            "最佳方案" if mode is None else ("普通" if mode == "normal" else "挑战") + f"第 {len(excluded)+1} 首歌")
        groups = self.groups(objective)
        stage_key = key + ":stages"
        previous = self.cache.get(stage_key) if self.cache else None
        frozen = previous.get("frozen", []) if previous else []
        for variables, values in zip((v for _, v in groups), frozen):
            for variable, value in zip(variables, values):
                cm.add(variable == value)
        plan = None
        for index, (expression, variables) in enumerate(groups):
            if index < len(frozen):
                continue
            self.solve_context = {"request_fingerprint": self.run_key, "objective": objective,
                                  "ranking_mode": mode, "excluded_songs": list(excluded),
                                  "lexicographic_stage": index}
            self.phase = ("证明收益最优" if index == 0 else "验证剩余 CP 与同收益排名")
            cm.maximize(expression)
            while True:
                self.emit()
                solver = self.native_solve(cm)
                with self.metrics.measure("oracle"):
                    plan, agrees = self.oracle(cm, solver)
                self.save()
                if agrees:
                    break
                self.stats["oracle_refinements"] += 1
                self.phase = "按实际技能评级继续验证"
            values = [solver.value(variable) for variable in variables]
            frozen.append(values)
            for variable, value in zip(variables, values):
                cm.add(variable == value)
            if self.cache:
                self.cache.put(stage_key, [], 0, frozen=frozen, plan=plan)
            self.check()
        if plan is None:
            plan = deepcopy(previous["plan"])
        if self.cache:
            self.cache.put(key, [], 0, proven=True, plan=plan)
        self.completed += 1
        self.step_keys.append(key)
        self.phase = "已完成最优证明"
        self.emit()
        self.save()
        self.check()
        return plan

    def run(self):
        saved = self.cache.get(self.run_key + ":complete") if self.cache else None
        if saved and saved.get("proven"):
            self.completed = self.cached_steps = self.total_steps
            self.prepared = self.cached_sheets = self.total_sheets
            self.prepared_keys, self.step_keys = saved["prepared_keys"], saved["step_keys"]
            result = deepcopy(saved["result"])
            result["search"].update(cached_complete_result=True, cached_steps=self.total_steps,
                                   cached_sheets=self.total_sheets, computed_sheets=0,
                                   elapsed_seconds=round(time.monotonic() - self.begin, 2))
            self.metrics.count("complete_result_cache_hits")
            self.emit()
            self.save("complete")
            return result
        self.save()
        try:
            self.build()
            plans, top3 = {}, {}
            for objective in OBJECTIVES:
                first = self.solve_step(objective)
                plans[objective] = first
                top3[objective] = {}
                for mode in MODES:
                    ranked = [first]
                    song_count = len({s["song_id"] for s in self.specs[mode]})
                    while len(ranked) < min(3, song_count):
                        ranked.append(self.solve_step(objective, mode, tuple(r[mode]["song_id"] for r in ranked)))
                    top3[objective][mode] = ranked
            counts = {mode: {"evaluated_bindings": self.member_teams * math.comb(len(self.sids), 5)
                             * (120 if self.specs[mode][0]["method"] == "ap" else 1) * len(self.specs[mode]),
                             "song_sheets": len(self.specs[mode]), "completed": True,
                             "optimization": dict(self.stats)} for mode in MODES}
            # Reuse the established response/assumption schema, then replace rankings
            # with independently proved full-cycle optima for every requested place.
            result = p.combine_plans({m: [plans[o][m] for o in OBJECTIVES] for m in MODES},
                                     self.settings["boost_budget"], self.settings["boost_per_live"],
                                     self.settings["starting_cp"], self.settings["challenge_cp"],
                                     self.request, self.data, counts, time.monotonic()-self.begin)
            result.update(plans=plans, top3=top3)
            ep, sp = plans["event_pt"]["totals"], plans["shop_pt"]["totals"]
            result["difference"] = {"event_pt_lost_with_shop_plan": ep["event_pt"] - sp["event_pt"],
                                    "shop_pt_lost_with_event_plan": sp["shop_pt"] - ep["shop_pt"],
                                    "identical_totals": (ep["event_pt"], ep["shop_pt"]) == (sp["event_pt"], sp["shop_pt"])}
            result["search"].update(algorithm="adaptive_cp_sat_with_exact_score_oracle", candidate_limit=None,
                                    binding_counts="full logical coverage proved by constraints; not enumerated bindings",
                                    total_sheets=self.total_sheets, cached_sheets=self.cached_sheets,
                                    computed_sheets=self.total_sheets-self.cached_sheets,
                                    completed_steps=self.completed, cached_steps=self.cached_steps,
                                    persistent_cache=self.cache is not None, optimality_proven=True,
                                    resume_policy="completed objectives, exact oracle facts and incumbent hints; restart current native search")
            self.save("complete")
            if self.cache:
                self.cache.put(self.run_key + ":complete", [], 0, proven=True, result=result,
                               prepared_keys=self.prepared_keys, step_keys=self.step_keys)
            return result
        except p.Cancelled:
            self.save("cancelled")
            raise


def optimize(request, data, model, specs, member_teams, progress, cancelled, cache, run_id, begin):
    return Search(request, data, model, specs, member_teams, progress, cancelled, cache, run_id, begin).run()
