"""关联分析、封闭模式与版本化存储的行为测试。"""
import itertools
import json
import random
import tempfile
import unittest
from pathlib import Path

from app.association import AssociationIndex
from app.catalog import load
from app.service import SCHEMAS
from app.store import QueryStore, StaleVersionError


def session(sid, available, chosen, stratum='日常', weight=1, visitor=None):
    return {
        'id': sid, 'revision': 1,
        'visitor': visitor or f'v-{sid}', 'stratum': stratum,
        'available': list(available), 'chosen': list(chosen), 'weight': weight,
    }


def payload(rows, data_version='t-1'):
    return {
        'schema_version': 1,
        'metadata': {'data_version': data_version},
        'collections': {'sessions': rows},
    }


def index(rows, data_version='t-1'):
    return AssociationIndex(load(payload(rows, data_version), SCHEMAS))


class OpportunityDomainTests(unittest.TestCase):
    def test_never_jointly_open_is_empty_domain_not_zero_participation(self):
        # C 与 D 各自开放但从不同时开放；E 开放过但无人参与。
        rows = [
            session('s1', ['C', 'E'], ['C']),
            session('s2', ['D', 'E'], ['D']),
        ]
        idx = index(rows)
        report = idx.combo_report(['C', 'D'])
        self.assertFalse(report['estimable'])
        self.assertIn('从未在同一会话同时开放', report['reason'])
        # “开放但没人去”与“根本没开放”区分开：E 有非空机会域、支持率为 0。
        e = idx.combo_report(['E'])
        self.assertTrue(e['estimable'])
        self.assertEqual(e['supporter_count'], 0)
        self.assertEqual(e['opportunity_support_rate'], 0.0)
        self.assertEqual(e['domain_session_ids'], ['s1', 's2'])

    def test_rule_denominator_is_the_joint_opportunity_domain(self):
        rows = [
            session('s1', ['A', 'B'], ['A', 'B']),
            session('s2', ['A', 'B'], ['A']),
            session('s3', ['A', 'B'], ['B']),
            session('s4', ['A', 'B'], []),
            session('s5', ['A'], ['A']),       # B 未开放，排除出规则机会域
            session('s6', ['B'], ['B']),       # A 未开放，排除出规则机会域
        ]
        m = index(rows).rule_report(['A'], ['B'])['rule_metrics']
        self.assertEqual(m['domain_session_count'], 4)
        self.assertEqual(m['domain_weight'], 4)
        self.assertAlmostEqual(m['joint_support_rate'], 0.25)
        self.assertAlmostEqual(m['confidence'], 0.5)
        # 独立基线必须是同一机会域内两个边际率的乘积：0.5 * 0.5
        self.assertAlmostEqual(m['independence_baseline_rate'], 0.25)
        self.assertAlmostEqual(m['lift'], 1.0)
        self.assertAlmostEqual(m['leverage'], 0.0)

    def test_rule_rejects_overlap_and_empty_sets(self):
        idx = index([session('s1', ['A', 'B'], ['A', 'B'])])
        with self.assertRaises(ValueError):
            idx.rule_report(['A'], ['A', 'B'])
        with self.assertRaises(ValueError):
            idx.rule_report([], ['B'])

    def test_weight_is_not_split_per_choice(self):
        rows = [session('s1', ['A', 'B'], ['A', 'B'], weight=3)]
        report = index(rows).combo_report(['A', 'B'])
        self.assertEqual(report['support_weight'], 3)
        self.assertEqual(report['domain_weight'], 3)


class ClosedPatternTests(unittest.TestCase):
    def _brute_force_closed(self, idx, min_weight=None, min_rate=None):
        """枚举幂集求标准答案：仅用于小数据对照，不进入生产路径。"""
        items = list(idx.items)
        closed = set()
        for size in range(1, len(items) + 1):
            for subset in itertools.combinations(items, size):
                tid = idx.supporters_of(subset)
                domain = idx.domain_of(subset)
                if not tid:
                    continue
                if min_weight is not None:
                    ok = idx._weight(tid) >= min_weight
                else:
                    ok = idx._weight(tid) / idx._weight(domain) >= min_rate
                if not ok:
                    continue
                is_closed = all(
                    idx.supporters_of(set(subset) | {other}) != tid
                    for other in items if other not in subset)
                if is_closed:
                    closed.add(subset)
        return closed

    def test_miner_matches_brute_force_on_all_threshold_bases(self):
        rng = random.Random(20261114)
        for seed in range(8):
            rng.seed(seed)
            rows = []
            for sid in range(1, 15):
                pool = [f'I{i}' for i in range(6)]
                available = {i for i in pool if rng.random() < 0.7}
                if not available:
                    available = {pool[0]}
                chosen = {i for i in available if rng.random() < 0.5}
                rows.append(session(f's{sid}', available, chosen,
                                    stratum=rng.choice(['日常', '节庆']),
                                    weight=rng.choice([1, 1, 2, 3])))
            idx = index(rows)
            for kwargs in ({'min_weight': 2}, {'min_rate': 0.4}):
                result = idx.closed_patterns(**kwargs)
                emitted = {tuple(p['items']) for p in result['patterns']}
                expected_closed = self._brute_force_closed(idx, **kwargs)
                self.assertEqual(emitted, expected_closed)
                # 所有发射模式确实达标，且支持者身份可追溯。
                for p in result['patterns']:
                    self.assertTrue(p['supporter_session_ids'])
                    self.assertEqual(
                        p['supporter_count'], len(p['supporter_session_ids']))

    def test_identical_supporters_are_absorbed_into_one_closure(self):
        rows = [
            session('s1', ['A', 'B'], ['A', 'B']),
            session('s2', ['A', 'B'], ['A', 'B']),
        ]
        result = index(rows).closed_patterns(min_weight=1)
        emitted = {tuple(p['items']) for p in result['patterns']}
        self.assertEqual(emitted, {('A', 'B')})
        # 两个单例与两两组合共三个达标节点共享同一支持 tidset，合并为一个闭包。
        self.assertEqual(result['search_stats']['equal_tidset_merges'], 2)

    def test_equal_weight_but_different_supporters_are_not_merged(self):
        rows = [
            session('s1', ['C', 'D'], ['C']),
            session('s2', ['C', 'D'], ['D']),
        ]
        result = index(rows).closed_patterns(min_weight=1)
        emitted = {tuple(p['items']) for p in result['patterns']}
        # 两个单例支持权重相同（各 1），但支持者身份不同，必须各自保留。
        self.assertEqual(emitted, {('C',), ('D',)})
        supporters = {tuple(p['items']): p['supporter_session_ids']
                      for p in result['patterns']}
        self.assertNotEqual(supporters[('C',)], supporters[('D',)])

    def test_rate_threshold_is_non_antimonotone_but_still_closed(self):
        rows = [
            session('s1', ['A', 'B'], ['A', 'B']),
            session('s2', ['A', 'B'], []),
            session('s3', ['A', 'B'], []),
        ]
        result = index(rows).closed_patterns(min_rate=0.3)
        emitted = {tuple(p['items']) for p in result['patterns']}
        # {A,B} 支持率 1/3 达标；单例 A、B 支持率同样 1/3 且 tidset 相同，
        # 封闭性要求只保留吸收后的 {A,B}。
        self.assertEqual(emitted, {('A', 'B')})

    def test_truncation_is_reported_honestly(self):
        rng = random.Random(7)
        rows = []
        for sid in range(60):
            available = [f'I{i}' for i in range(12) if rng.random() < 0.8]
            if not available:
                available = ['I0']
            chosen = rng.sample(available, k=min(3, len(available)))
            rows.append(session(f's{sid}', available, chosen))
        idx = index(rows)
        result = idx.closed_patterns(min_rate=0.01, max_nodes=5)
        self.assertTrue(result['search_stats']['truncated'])
        self.assertIn('节点上限', result['search_stats']['truncation_reason'])
        self.assertIn('截断', result['search_stats']['closure_guarantee'])

    def test_threshold_validation(self):
        idx = index([session('s1', ['A'], ['A'])])
        with self.assertRaises(ValueError):
            idx.closed_patterns()
        with self.assertRaises(ValueError):
            idx.closed_patterns(min_weight=1, min_rate=0.5)
        with self.assertRaises(ValueError):
            idx.closed_patterns(min_rate=1.5)


class StratumExplanationTests(unittest.TestCase):
    def test_single_stratum_support_flagged(self):
        rows = [
            session('f1', ['A', 'B'], ['A', 'B'], '节庆'),
            session('f2', ['A', 'B'], ['A', 'B'], '节庆'),
            session('n1', ['A', 'B'], ['A'], '日常'),
            session('n2', ['A', 'B'], ['B'], '日常'),
        ]
        explanation = index(rows).rule_report(['A'], ['B'])['association_explanation']
        self.assertEqual(explanation['code'], 'single_stratum_support')
        self.assertEqual(explanation['support_strata'], ['节庆'])
        self.assertEqual(explanation['open_strata'], ['日常', '节庆'])

    def test_composition_effect_simpson_style(self):
        rows = []
        # 节庆：2×2 独立表（pA=pB=0.75），层内 lift 恰为 1。
        rows += [session(f'fb{i}', ['A', 'B'], ['A', 'B'], '节庆') for i in range(9)]
        rows += [session(f'fa{i}', ['A', 'B'], ['A'], '节庆') for i in range(3)]
        rows += [session(f'fc{i}', ['A', 'B'], ['B'], '节庆') for i in range(3)]
        rows += [session(f'fn{i}', ['A', 'B'], [], '节庆') for i in range(1)]
        # 日常：同样独立但边际更低（pA=pB=0.25），层内 lift 仍为 1。
        rows += [session(f'nb{i}', ['A', 'B'], ['A', 'B'], '日常') for i in range(1)]
        rows += [session(f'na{i}', ['A', 'B'], ['A'], '日常') for i in range(3)]
        rows += [session(f'nc{i}', ['A', 'B'], ['B'], '日常') for i in range(3)]
        rows += [session(f'nn{i}', ['A', 'B'], [], '日常') for i in range(9)]
        report = index(rows).rule_report(['A'], ['B'])
        m = report['rule_metrics']
        self.assertGreater(m['lift'], 1.0)
        for sub in report['stratum_metrics'].values():
            self.assertTrue(sub['lift'] is None or sub['lift'] <= 1.0 + 1e-12)
        self.assertEqual(report['association_explanation']['code'],
                         'composition_effect')

    def test_consistent_across_strata(self):
        rows = [
            *[session(f'f{i}', ['A', 'B'], ['A', 'B'], '节庆') for i in range(1, 3)],
            *[session(f'f{i}', ['A', 'B'], [], '节庆') for i in range(3, 5)],
            *[session(f'n{i}', ['A', 'B'], ['A', 'B'], '日常') for i in range(1, 3)],
            *[session(f'n{i}', ['A', 'B'], [], '日常') for i in range(3, 5)],
        ]
        report = index(rows).rule_report(['A'], ['B'])
        self.assertEqual(report['association_explanation']['code'],
                         'consistent_across_strata')
        for sub in report['stratum_metrics'].values():
            self.assertAlmostEqual(sub['lift'], 2.0)


class VersioningTests(unittest.TestCase):
    def _specs(self):
        return [
            {'name': 'q-rule', 'type': 'rule',
             'antecedent': ['A'], 'consequent': ['B']},
            {'name': 'q-closed', 'type': 'closed_patterns', 'min_weight': 1},
        ]

    def test_revision_rebuild_rejects_stale_and_removes_old_closure(self):
        rows = [
            session('s1', ['A', 'B'], ['A', 'B']),
            session('s2', ['A', 'B'], ['A']),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = QueryStore(tmp)
            old = payload(rows, 'v-1')
            index_v1, _, removed = store.rebuild_from_payload(old, SCHEMAS, self._specs())
            self.assertEqual(removed, [])
            digest_v1 = index_v1.input_digest
            self.assertEqual(store.versions(), [digest_v1])

            # 修订：撤回 s2 并以新修订号恢复为不同开放记录。
            revised_rows = rows + [
                {'id': 's2', 'revision': 2, 'deleted': True},
                {**session('s2', ['A'], ['A']), 'revision': 3},
            ]
            new = payload(revised_rows, 'v-2')
            index_v2, results, removed = store.rebuild_from_payload(
                new, SCHEMAS, self._specs())
            digest_v2 = index_v2.input_digest
            self.assertNotEqual(digest_v1, digest_v2)
            self.assertEqual(removed, [digest_v1])
            self.assertEqual(store.versions(), [digest_v2])

            # 旧版本目录已被整体清除；用旧 digest 读取会得到明确的过时诊断，
            # 不会静默回退到旧闭包。
            with self.assertRaises(StaleVersionError):
                store.load('q-closed', digest_v1)
            # 当前版本的结果可正常读取，且绑定新 digest。
            fresh = store.load('q-closed', digest_v2)
            self.assertEqual(fresh['input_digest'], digest_v2)
            self.assertEqual({name for name, _ in results},
                             {'q-rule', 'q-closed'})

    def test_stale_payload_is_refused_even_if_file_remains(self):
        rows = [session('s1', ['A', 'B'], ['A', 'B'])]
        with tempfile.TemporaryDirectory() as tmp:
            store = QueryStore(tmp)
            idx = index(rows)
            store.save(idx.input_digest, 'q', idx.combo_report(['A']))
            other_digest = 'f' * 64
            with self.assertRaises(StaleVersionError) as ctx:
                store.load('q', other_digest)
            self.assertEqual(ctx.exception.saved_digest, idx.input_digest)
            self.assertEqual(ctx.exception.current_digest, other_digest)

    def test_save_rejects_cross_digest_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = QueryStore(tmp)
            with self.assertRaises(ValueError):
                store.save('a' * 64, 'q', {'input_digest': 'b' * 64})

    def test_input_order_does_not_change_results(self):
        rows = [
            session('s2', ['A', 'B'], ['B']),
            session('s1', ['A', 'B'], ['A', 'B']),
            session('s3', ['A'], ['A']),
        ]
        first = index(rows).closed_patterns(min_weight=1)
        second = index(list(reversed(rows))).closed_patterns(min_weight=1)
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True))


class SparseLongTailScaleTests(unittest.TestCase):
    def test_reports_candidate_scale_and_memory_without_powerset(self):
        rng = random.Random(99)
        item_count, session_count = 300, 2000
        rows = []
        for sid in range(session_count):
            # 幂律：少数热门业态 + 大量稀疏长尾。
            head = [f'H{i}' for i in range(20) if rng.random() < 0.5]
            tail = [f'T{i}' for i in range(item_count - 20)
                    if rng.random() < 0.02]
            available = head + tail
            if not available:
                available = ['H0']
            chosen = [i for i in available if rng.random() < 0.4]
            rows.append(session(f's{sid}', available, chosen,
                                stratum='节庆' if sid % 10 == 0 else '日常',
                                weight=rng.choice([1, 2, 5])))
        idx = index(rows)
        result = idx.closed_patterns(min_weight=20)
        stats = result['search_stats']
        self.assertFalse(stats['truncated'])
        # 实际候选规模与内存必须如实报告，且远小于 2^300 的幂集。
        self.assertLess(len(result['patterns']), 5_000)
        self.assertLess(stats['nodes_visited'], stats['root_items'] ** 2)
        self.assertGreater(stats['index_memory_bytes'], 0)
        self.assertGreater(stats['intersections_evaluated'], 0)
        # 每个闭包带可追溯的支持者与机会域摘要。
        for p in result['patterns']:
            self.assertIn('supporter_session_ids', p)
            self.assertIn('domain_session_count', p)
            self.assertIn('strata', p)
        # 率门槛在同一数据上也可运行并受节点上限保护。
        capped = idx.closed_patterns(min_rate=0.3, max_nodes=2_000)
        self.assertIn('truncated', capped['search_stats'])
        # 内存数字量级合理：索引整体应远小于每会话全量幂集的假想开销。
        self.assertLess(stats['index_memory_bytes'], 50 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
