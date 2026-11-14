"""按资料版本持久化查询结果：索引与已保存查询绑定明确 input_digest。

资料修订（撤回或修改开放记录）会改变 Snapshot.digest；跨版本读取直接抛出
StaleVersionError，rebuild 到新版本后旧 digest 目录整体清除，不残留旧闭包。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .association import AssociationIndex
from .catalog import load

_SAFE_NAME = re.compile(r'^[A-Za-z0-9_.\-一-鿿]+$')


class StaleVersionError(Exception):
    """保存的查询属于另一资料版本，必须在新版本上重算。"""

    def __init__(self, query_name, saved_digest, current_digest):
        super().__init__(
            f'查询 {query_name!r} 基于资料 {saved_digest[:12]}，当前资料为 '
            f'{current_digest[:12]}；请先在新版本上 rebuild，不读取旧闭包。')
        self.saved_digest = saved_digest
        self.current_digest = current_digest


class QueryStore:
    """目录结构：root/<digest>/<query>.json，另存每个版本的索引摘要。"""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 版本目录

    def versions(self):
        return sorted(p.name for p in self.root.iterdir()
                      if p.is_dir() and re.fullmatch(r'[0-9a-f]{64}', p.name))

    def _version_dir(self, digest):
        return self.root / digest

    def list_queries(self, digest=None):
        result = {}
        for version in ([digest] if digest else self.versions()):
            vdir = self._version_dir(version)
            if not vdir.is_dir():
                continue
            result[version] = sorted(
                p.stem for p in vdir.glob('*.json') if p.stem != '_index_info')
        return result

    # ------------------------------------------------------------ 保存与读取

    def save(self, digest, name, payload):
        if not _SAFE_NAME.match(name):
            raise ValueError('查询名称仅允许字母数字、下划线、点、连字符与中文')
        if payload.get('input_digest') != digest:
            raise ValueError('结果的 input_digest 与目标版本不一致，拒绝保存')
        vdir = self._version_dir(digest)
        vdir.mkdir(parents=True, exist_ok=True)
        target = vdir / f'{name}.json'
        tmp = target.with_suffix('.json.tmp')
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, indent=2),
            encoding='utf-8')
        os.replace(tmp, target)
        return str(target)

    def load(self, name, current_digest):
        """只在指定资料版本目录内读取；版本不符或版本已清除都不返回旧闭包。"""
        candidate = self._version_dir(current_digest) / f'{name}.json'
        if not candidate.exists():
            # 若同名查询存在于其他（已过时）版本，明确给出版本不匹配诊断。
            for version in self.versions():
                if (self._version_dir(version) / f'{name}.json').exists():
                    raise StaleVersionError(name, version, current_digest)
            raise KeyError(f'未找到已保存查询：{name}')
        payload = json.loads(candidate.read_text(encoding='utf-8'))
        saved_digest = payload.get('input_digest')
        if saved_digest != current_digest:
            raise StaleVersionError(name, saved_digest, current_digest)
        return payload

    def write_index_info(self, index: AssociationIndex):
        vdir = self._version_dir(index.input_digest)
        vdir.mkdir(parents=True, exist_ok=True)
        info = {
            'input_digest': index.input_digest,
            'data_version': index.data_version,
            'session_count': len(index.sessions),
            'item_count': len(index.items),
            'strata': list(index.strata),
            'index_memory_bytes': index.index_memory_bytes,
        }
        tmp = vdir / '_index_info.json.tmp'
        target = vdir / '_index_info.json'
        tmp.write_text(json.dumps(info, ensure_ascii=False,
                                  sort_keys=True, indent=2), encoding='utf-8')
        os.replace(tmp, target)

    # ------------------------------------------------------------ 重算与清理

    def rebuild(self, snapshot, schemas, specs):
        """在 snapshot 的明确版本上重算全部查询，并清除其他版本的旧闭包。

        specs 为查询规格列表：
          {'name': str, 'type': 'combination', 'items': [...]}
          {'name': str, 'type': 'rule',
           'antecedent': [...], 'consequent': [...]}
          {'name': str, 'type': 'closed_patterns',
           'min_weight': 可选, 'min_rate': 可选,
           'max_nodes': 可选, 'domain_id_cap': 可选}
        """
        index = AssociationIndex(snapshot)
        self.write_index_info(index)
        results = []
        for spec in specs:
            payload = self._run_spec(index, spec)
            self.save(index.input_digest, spec['name'], payload)
            results.append((spec['name'], payload))
        removed = self._prune_other_versions(index.input_digest)
        return index, results, removed

    def rebuild_from_payload(self, payload, schemas, specs):
        return self.rebuild(load(payload, schemas), schemas, specs)

    @staticmethod
    def _run_spec(index, spec):
        kind = spec['type']
        if kind == 'combination':
            return index.combo_report(spec['items'])
        if kind == 'rule':
            return index.rule_report(spec['antecedent'], spec['consequent'])
        if kind == 'closed_patterns':
            kwargs = {}
            for key in ('min_weight', 'min_rate', 'max_nodes', 'domain_id_cap'):
                if key in spec:
                    kwargs[key] = spec[key]
            return index.closed_patterns(**kwargs)
        raise ValueError(f'未知查询类型：{kind}')

    def _prune_other_versions(self, keep_digest):
        import shutil
        removed = []
        for version in self.versions():
            if version != keep_digest:
                shutil.rmtree(self._version_dir(version))
                removed.append(version)
        return removed
