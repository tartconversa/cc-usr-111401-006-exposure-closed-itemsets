"""机会域关联分析与封闭模式检索（仅使用标准库）。

口径约定（与 docs/data-contract.md 一致）：

* 组合 X 的机会域 D(X) 是 X 中全部业态同时开放的会话；支持率只在 D(X) 内
  计算，从未同时开放的业态得到空机会域而不是被当作“开放但无人参与”。
* 规则 A→B 的全部指标使用 D(A∪B) 这一前件后件共同可参与的同一机会域；
  独立基线是该域内两个边际参与率的乘积，不使用各业态在自身分母上的比例。
* 每条会话携带单一游客权重，不按选择项数分摊。
* 封闭性以未加权的支持会话身份集合判断；权重只参与门槛，加权支持数相同但
  支持者不同的组合不会被合并。

本模块只报告观察到的相关关系，不暗示因果，也不预设任何组合适合联营。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass


def _intersect(left, right):
    """两个有序会话索引元组的归并交集。"""
    i = j = 0
    out = []
    while i < len(left) and j < len(right):
        a, b = left[i], right[j]
        if a == b:
            out.append(a)
            i += 1
            j += 1
        elif a < b:
            i += 1
        else:
            j += 1
    return tuple(out)


def deep_size(obj, seen=None):
    """估算容器结构的实际驻留字节数（含稀疏长尾 posting 的真实开销）。"""
    if seen is None:
        seen = set()
    marker = id(obj)
    if marker in seen:
        return 0
    seen.add(marker)
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        size += sum(deep_size(k, seen) + deep_size(v, seen) for k, v in obj.items())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(deep_size(v, seen) for v in obj)
    return size


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    visitor: str
    stratum: str
    weight: float
    available: frozenset
    chosen: frozenset


class AssociationIndex:
    """绑定明确资料版本的机会域索引。

    索引由 Snapshot 构建，携带 input_digest；资料修订后 digest 改变，调用方
    必须重建索引，持久化侧的旧版本由 app.store 拒绝或清除。
    """

    def __init__(self, snapshot):
        self.input_digest = snapshot.digest
        self.data_version = snapshot.metadata.get('data_version')
        records = []
        for row in snapshot.rows('sessions'):
            available = frozenset(row['available'])
            chosen = frozenset(row['chosen'])
            if not chosen <= available:
                raise ValueError('参与集合或权重无效：chosen 必须是 available 子集')
            weight = row['weight']
            if not isinstance(weight, (int, float)) or weight <= 0:
                raise ValueError('参与集合或权重无效：权重必须为正数')
            records.append(SessionRecord(
                session_id=str(row['id']), visitor=row['visitor'],
                stratum=row['stratum'], weight=weight,
                available=available, chosen=chosen))
        # 按会话身份排序，保证交集与输出不依赖输入顺序。
        records.sort(key=lambda r: r.session_id)
        self.sessions = tuple(records)
        self._ids = tuple(r.session_id for r in records)
        self._weights = tuple(r.weight for r in records)
        self._strata = tuple(r.stratum for r in records)

        domain_lists: dict[str, list[int]] = {}
        support_lists: dict[str, list[int]] = {}
        stratum_sessions: dict[str, list[int]] = {}
        for idx, record in enumerate(records):
            for item in record.available:
                domain_lists.setdefault(item, []).append(idx)
            for item in record.chosen:
                support_lists.setdefault(item, []).append(idx)
            stratum_sessions.setdefault(record.stratum, []).append(idx)
        # 冻结为有序元组：稀疏长尾业态只占自身 posting 长度的内存。
        self.domain_postings = {k: tuple(v) for k, v in sorted(domain_lists.items())}
        self.support_postings = {k: tuple(v) for k, v in sorted(support_lists.items())}
        self.items = tuple(sorted(domain_lists))
        self.strata = tuple(sorted(stratum_sessions))
        self._stratum_index = {k: tuple(v) for k, v in sorted(stratum_sessions.items())}
        self.index_memory_bytes = deep_size({
            'sessions': self.sessions,
            'domain_postings': self.domain_postings,
            'support_postings': self.support_postings,
            'stratum_index': self._stratum_index,
        })

    # ------------------------------------------------------------------ 基础集合

    def _weight(self, tid):
        weights = self._weights
        return sum(weights[i] for i in tid)

    def domain_of(self, items):
        """D(X)：组合内全部业态同时开放的会话索引（有序元组）。"""
        result = None
        for item in items:
            posting = self.domain_postings.get(item)
            if not posting:
                return ()
            result = posting if result is None else _intersect(result, posting)
            if not result:
                return ()
        return result or ()

    def supporters_of(self, items):
        """S(X)：组合内全部业态均实际参与的会话索引（有序元组）。"""
        result = None
        for item in items:
            posting = self.support_postings.get(item)
            if not posting:
                return ()
            result = posting if result is None else _intersect(result, posting)
            if not result:
                return ()
        return result or ()

    def _breakdown(self, tid):
        """按分层汇总一段会话索引的计数与权重。"""
        out = {}
        for idx in tid:
            entry = out.setdefault(self._strata[idx], {
                'session_count': 0, 'weight': 0.0})
            entry['session_count'] += 1
            entry['weight'] += self._weights[idx]
        return {stratum: out[stratum] for stratum in sorted(out)}

    # -------------------------------------------------------------- 组合查询

    def combo_report(self, items):
        """任意业态组合的机会域支持报告。"""
        combo = tuple(sorted(set(items)))
        if not combo:
            raise ValueError('组合至少包含一个业态')
        domain = self.domain_of(combo)
        report = {
            'kind': 'combination',
            'input_digest': self.input_digest,
            'data_version': self.data_version,
            'items': list(combo),
            'estimable': bool(domain),
        }
        if not domain:
            report['reason'] = '机会域为空：这些业态从未在同一会话同时开放'
            return report
        supporters = self.supporters_of(combo)
        domain_weight = self._weight(domain)
        support_weight = self._weight(supporters)
        report.update({
            'domain_session_ids': [self._ids[i] for i in domain],
            'domain_session_count': len(domain),
            'domain_weight': domain_weight,
            'supporter_session_ids': [self._ids[i] for i in supporters],
            'supporter_count': len(supporters),
            'support_weight': support_weight,
            'opportunity_support_rate': support_weight / domain_weight,
            'strata': self._combo_strata(domain, supporters),
        })
        return report

    def _combo_strata(self, domain, supporters):
        result = {}
        for stratum, idx_list in self._stratum_index.items():
            d = _intersect(domain, idx_list)
            t = _intersect(supporters, idx_list)
            dw = self._weight(d)
            if dw == 0:
                continue
            result[stratum] = {
                'domain_session_count': len(d),
                'domain_weight': dw,
                'supporter_count': len(t),
                'support_weight': self._weight(t),
                'opportunity_support_rate': self._weight(t) / dw,
            }
        return result

    # -------------------------------------------------------------- 规则查询

    def rule_report(self, antecedent, consequent):
        """规则 A→B 的同机会域指标与分层解释。"""
        left = tuple(sorted(set(antecedent)))
        right = tuple(sorted(set(consequent)))
        if not left or not right:
            raise ValueError('前件与后件均不能为空')
        if set(left) & set(right):
            raise ValueError('前件与后件必须不相交')
        union = tuple(sorted(set(left) | set(right)))
        domain = self.domain_of(union)
        report = {
            'kind': 'rule',
            'input_digest': self.input_digest,
            'data_version': self.data_version,
            'antecedent': list(left),
            'consequent': list(right),
            'items': list(union),
            'estimable': bool(domain),
        }
        if not domain:
            report['reason'] = '机会域为空：前件后件从未在同一会话同时开放'
            return report
        # 关键口径：前件的参与计数也限制在共同机会域 D(A∪B) 内。
        chose_left = _intersect(self.supporters_of(left), domain)
        chose_right = _intersect(self.supporters_of(right), domain)
        chose_both = self.supporters_of(union)
        report['rule_metrics'] = self._rule_metrics(domain, chose_left, chose_right, chose_both)
        report['stratum_metrics'] = {}
        for stratum, idx_list in self._stratum_index.items():
            sub_domain = _intersect(domain, idx_list)
            if not sub_domain:
                continue
            report['stratum_metrics'][stratum] = self._rule_metrics(
                sub_domain,
                _intersect(chose_left, sub_domain),
                _intersect(chose_right, sub_domain),
                _intersect(chose_both, sub_domain))
        report['association_explanation'] = self._explain_strata(report)
        return report

    def _rule_metrics(self, domain, chose_left, chose_right, chose_both):
        domain_weight = self._weight(domain)
        w_left = self._weight(chose_left)
        w_right = self._weight(chose_right)
        w_both = self._weight(chose_both)
        p_left = w_left / domain_weight
        p_right = w_right / domain_weight
        p_both = w_both / domain_weight
        # 独立基线：同一机会域内两个边际率的乘积。
        independence_baseline = p_left * p_right
        return {
            'domain_session_count': len(domain),
            'domain_weight': domain_weight,
            'antecedent_weight': w_left,
            'consequent_weight': w_right,
            'joint_support_weight': w_both,
            'joint_support_rate': p_both,
            'confidence': (w_both / w_left) if w_left > 0 else None,
            'independence_baseline_rate': independence_baseline,
            'lift': (p_both / independence_baseline)
                    if independence_baseline > 0 else None,
            'leverage': p_both - independence_baseline,
        }

    def _explain_strata(self, report):
        """判断总体相关是否仅来自某个分层（相关而非因果的描述性判定）。"""
        overall = report['rule_metrics']
        strata = report['stratum_metrics']
        support_strata = sorted(
            name for name, m in strata.items()
            if m['joint_support_weight'] > 0)
        open_strata = sorted(strata)
        if not support_strata:
            code = 'no_support'
            detail = '共同机会域内没有任何共同参与会话，无法支持相关判断。'
        elif len(support_strata) == 1 and len(open_strata) > 1:
            only = support_strata[0]
            code = 'single_stratum_support'
            detail = (f'共同参与仅出现在「{only}」分层；其余分层虽同时开放却无共同'
                      f'参与，总体相关可能完全由该分层构成，分层外不具重复性。')
        else:
            lifts = [m['lift'] for m in strata.values() if m['lift'] is not None]
            overall_lift = overall['lift']
            if overall_lift is not None and lifts and all(l <= 1.0 + 1e-12 for l in lifts) \
                    and overall_lift > 1.0 + 1e-12:
                code = 'composition_effect'
                detail = ('总体 lift 高于任一可估计分层内的 lift：相关来自分层混合'
                          '（各分层权重构成不同），而非分层内部的共同选择倾向。')
            elif lifts and all(
                    (l >= 1.0 - 1e-12) == (lifts[0] >= 1.0 - 1e-12) for l in lifts):
                code = 'consistent_across_strata'
                detail = '各可估计分层内 lift 方向一致，相关并非仅由单一分层造成。'
            else:
                code = 'mixed_or_insufficient'
                detail = '各分层方向不一致或样本不足，需结合分层指标谨慎解读。'
        return {
            'code': code,
            'support_strata': support_strata,
            'open_strata': open_strata,
            'detail': detail,
            'note': '该判定仅描述会话内的共同参与相关，不表示因果或联营效果。',
        }

    # -------------------------------------------------------------- 封闭模式挖掘

    def closed_patterns(self, min_weight=None, min_rate=None,
                        max_nodes=100_000, domain_id_cap=100):
        """发现达到支持门槛的封闭组合。

        min_weight 与 min_rate 必须且只能声明一个：
        * min_weight：支持总权重门槛（反单调，可按权重剪枝）；
        * min_rate：各组合自身机会域支持率门槛（非反单调，仅剪零支持分支，
          依赖 tidset 合并与节点上限控制规模，截断时 truncated 置真）。
        封闭性始终按完整资料的未加权支持会话身份集合相等判断。

        实现：Eclat 后缀枚举全部达标节点（不展开幂集，交集即时剪枝），再按
        精确支持 tidset 合并——任一非闭包节点的闭包与其同 tidset 且同样达标，
        必在枚举中出现，因此每个 tidset 取并集业态恰好得到全部闭项集。
        """
        basis, threshold = self._validate_threshold(min_weight, min_rate)
        stats = {
            'threshold_basis': basis,
            'threshold': threshold,
            'root_items': len(self.items),
            'frequent_roots': 0,
            'nodes_visited': 0,
            'intersections_evaluated': 0,
            'pruned_zero_support': 0,
            'pruned_below_weight': 0,
            'equal_tidset_merges': 0,
            'qualified_nodes': 0,
            'max_frontier': 0,
            'truncated': False,
            'truncation_reason': None,
            'index_memory_bytes': self.index_memory_bytes,
            'session_count': len(self.sessions),
        }
        roots = self._build_roots(basis, threshold, stats)
        # tidset -> {'items': set, 'domain': 有序会话索引元组}
        by_tid: dict[tuple, dict] = {}
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, len(self.items) + 100))
        try:
            for name, tid, domain, entries in roots:
                self._dfs((name,), tid, domain, entries,
                          basis, threshold, max_nodes, stats, by_tid)
                if stats['truncated']:
                    break
        finally:
            sys.setrecursionlimit(old_limit)
        if stats['truncated']:
            # 截断时并集可能不完整，不能宣称封闭性；只返回已观察节点并明确标注。
            stats['closure_guarantee'] = (
                '截断：以下模式仅为已访问节点，不保证封闭性完整，请提高门槛或上限')
        else:
            stats['closure_guarantee'] = '完整：全部封闭组合已按支持会话身份集合闭包'
        patterns = [self._pattern_payload(tid, info, basis, threshold, domain_id_cap)
                    for tid, info in by_tid.items()]
        patterns.sort(key=lambda p: (-p['support_weight'], p['items']))
        return {
            'kind': 'closed_patterns',
            'input_digest': self.input_digest,
            'data_version': self.data_version,
            'threshold': {'basis': basis, 'value': threshold},
            'patterns': patterns,
            'search_stats': stats,
        }

    @staticmethod
    def _validate_threshold(min_weight, min_rate):
        if (min_weight is None) == (min_rate is None):
            raise ValueError('必须且只能声明一种门槛：min_weight 或 min_rate')
        if min_weight is not None:
            if not isinstance(min_weight, (int, float)) or min_weight <= 0:
                raise ValueError('min_weight 必须为正数')
            return 'support_weight', min_weight
        if not isinstance(min_rate, (int, float)) or not 0 < min_rate <= 1:
            raise ValueError('min_rate 必须落在 (0, 1]')
        return 'opportunity_support_rate', min_rate

    def _build_roots(self, basis, threshold, stats):
        roots = []
        for pos, item in enumerate(self.items):
            tid = self.support_postings.get(item, ())
            if not tid:
                stats['pruned_zero_support'] += 1
                continue
            if basis == 'support_weight' and self._weight(tid) < threshold:
                stats['pruned_below_weight'] += 1
                continue
            entries = []
            for other in self.items[pos + 1:]:
                other_tid = self.support_postings.get(other, ())
                stats['intersections_evaluated'] += 1
                inter = _intersect(tid, other_tid)
                if not inter:
                    stats['pruned_zero_support'] += 1
                    continue
                if basis == 'support_weight' and self._weight(inter) < threshold:
                    stats['pruned_below_weight'] += 1
                    continue
                entries.append((other, inter,
                                _intersect(self.domain_postings[item],
                                           self.domain_postings[other])))
            stats['frequent_roots'] += 1
            stats['max_frontier'] = max(stats['max_frontier'], len(entries))
            roots.append((item, tid, self.domain_postings[item], entries))
        return roots

    def _register(self, prefix, tid, domain_tid, stats, by_tid):
        stats['qualified_nodes'] += 1
        existing = by_tid.get(tid)
        if existing is None:
            by_tid[tid] = {'items': set(prefix), 'domain': domain_tid}
        else:
            # 相同支持会话身份集合才合并；权重相同但支持者不同的 tid 不同。
            existing['items'].update(prefix)
            existing['domain'] = _intersect(existing['domain'], domain_tid)
            stats['equal_tidset_merges'] += 1

    def _dfs(self, prefix, tid, domain_tid, entries,
             basis, threshold, max_nodes, stats, by_tid):
        stats['nodes_visited'] += 1
        if stats['nodes_visited'] >= max_nodes:
            stats['truncated'] = True
            stats['truncation_reason'] = f'达到节点上限 {max_nodes}'
            self._register(prefix, tid, domain_tid, stats, by_tid)
            return
        if self._qualified(tid, domain_tid, basis, threshold):
            self._register(prefix, tid, domain_tid, stats, by_tid)
        for k, (name, child_tid, child_domain) in enumerate(entries):
            child_entries = []
            for other_name, other_tid, other_domain in entries[k + 1:]:
                stats['intersections_evaluated'] += 1
                inter = _intersect(child_tid, other_tid)
                if not inter:
                    stats['pruned_zero_support'] += 1
                    continue
                if basis == 'support_weight' and self._weight(inter) < threshold:
                    stats['pruned_below_weight'] += 1
                    continue
                child_entries.append((
                    other_name, inter, _intersect(child_domain, other_domain)))
            stats['max_frontier'] = max(stats['max_frontier'], len(child_entries))
            self._dfs(prefix + (name,), child_tid, child_domain, child_entries,
                      basis, threshold, max_nodes, stats, by_tid)
            if stats['truncated']:
                return

    def _qualified(self, tid, domain_tid, basis, threshold):
        if basis == 'support_weight':
            return self._weight(tid) >= threshold
        return self._weight(tid) / self._weight(domain_tid) >= threshold

    def _pattern_payload(self, tid, info, basis, threshold, domain_id_cap):
        prefix = tuple(sorted(info['items']))
        domain_tid = info['domain']
        domain_weight = self._weight(domain_tid)
        support_weight = self._weight(tid)
        domain_ids = [self._ids[i] for i in domain_tid]
        return {
            'items': list(prefix),
            'supporter_session_ids': [self._ids[i] for i in tid],
            'supporter_count': len(tid),
            'support_weight': support_weight,
            'domain_session_ids_sample': domain_ids[:domain_id_cap],
            'domain_id_truncated': len(domain_ids) > domain_id_cap,
            'domain_session_count': len(domain_tid),
            'domain_weight': domain_weight,
            'opportunity_support_rate': support_weight / domain_weight,
            'strata': self._combo_strata(domain_tid, tid),
            'threshold': {'basis': basis, 'value': threshold},
        }
