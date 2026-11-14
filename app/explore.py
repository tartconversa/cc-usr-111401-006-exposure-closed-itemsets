"""命令行入口：机会域关联查询、封闭模式检索与版本化查询存储。

示例：
    python -m app.explore combo fixtures/sample.json --items 工坊,茶室
    python -m app.explore rule  fixtures/sample.json --antecedent 工坊 --consequent 茶室
    python -m app.explore closed fixtures/sample.json --min-weight 1
    python -m app.explore closed fixtures/sample.json --min-rate 0.5
    python -m app.explore rebuild fixtures/sample.json --store .querystore --spec specs.json
    python -m app.explore saved --store .querystore --digest <64位digest> --name q1
"""
import argparse
import json
from pathlib import Path

from .catalog import load
from .association import AssociationIndex
from .store import QueryStore, StaleVersionError
from .service import SCHEMAS


def _load_index(path):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    snapshot = load(payload, SCHEMAS)
    return AssociationIndex(snapshot)


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=2))


def _split(values):
    return [v.strip() for v in values.split(',') if v.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='app.explore',
        description='机会域关联分析与封闭模式检索（输出描述相关，不作因果推断）')
    sub = parser.add_subparsers(dest='command', required=True)

    p_combo = sub.add_parser('combo', help='查询给定业态组合的机会域支持')
    p_combo.add_argument('payload')
    p_combo.add_argument('--items', required=True, help='逗号分隔的业态名称')

    p_rule = sub.add_parser('rule', help='查询前件→后件规则的同域指标与分层解释')
    p_rule.add_argument('payload')
    p_rule.add_argument('--antecedent', required=True)
    p_rule.add_argument('--consequent', required=True)

    p_closed = sub.add_parser('closed', help='检索达到支持门槛的封闭组合')
    p_closed.add_argument('payload')
    threshold = p_closed.add_mutually_exclusive_group(required=True)
    threshold.add_argument('--min-weight', type=float)
    threshold.add_argument('--min-rate', type=float)
    p_closed.add_argument('--max-nodes', type=int, default=100_000)
    p_closed.add_argument('--domain-id-cap', type=int, default=100)

    p_rebuild = sub.add_parser('rebuild', help='在明确资料版本上重算并保存查询，清除旧版本')
    p_rebuild.add_argument('payload')
    p_rebuild.add_argument('--store', required=True)
    p_rebuild.add_argument('--spec', required=True, help='查询规格 JSON 文件')

    p_list = sub.add_parser('list', help='列出各资料版本下的已保存查询')
    p_list.add_argument('--store', required=True)

    p_saved = sub.add_parser('saved', help='读取已保存查询（版本不符即拒绝）')
    p_saved.add_argument('--store', required=True)
    p_saved.add_argument('--name', required=True)
    p_saved.add_argument('--digest', required=True, help='当前资料的 input_digest')

    args = parser.parse_args(argv)

    if args.command == 'combo':
        _emit(_load_index(args.payload).combo_report(_split(args.items)))
    elif args.command == 'rule':
        _emit(_load_index(args.payload).rule_report(
            _split(args.antecedent), _split(args.consequent)))
    elif args.command == 'closed':
        kwargs = {'max_nodes': args.max_nodes, 'domain_id_cap': args.domain_id_cap}
        if args.min_weight is not None:
            kwargs['min_weight'] = args.min_weight
        else:
            kwargs['min_rate'] = args.min_rate
        _emit(_load_index(args.payload).closed_patterns(**kwargs))
    elif args.command == 'rebuild':
        payload = json.loads(Path(args.payload).read_text(encoding='utf-8'))
        specs = json.loads(Path(args.spec).read_text(encoding='utf-8'))
        store = QueryStore(args.store)
        index, results, removed = store.rebuild_from_payload(payload, SCHEMAS, specs)
        _emit({
            'input_digest': index.input_digest,
            'data_version': index.data_version,
            'recomputed_queries': [name for name, _ in results],
            'removed_old_versions': removed,
            'index_memory_bytes': index.index_memory_bytes,
        })
    elif args.command == 'list':
        _emit(QueryStore(args.store).list_queries())
    elif args.command == 'saved':
        try:
            _emit(QueryStore(args.store).load(args.name, args.digest))
        except StaleVersionError as exc:
            raise SystemExit(str(exc))


if __name__ == '__main__':
    main()
