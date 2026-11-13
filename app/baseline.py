"""现有业务摘要，不推断未观察到的业务关系。"""
def analyze(snapshot):
    counts = {}
    for row in snapshot.rows('sessions'):
        available, chosen = set(row['available']), set(row['chosen'])
        if not chosen <= available or row['weight'] <= 0:
            raise ValueError('参与集合或权重无效')
        for item in available:
            a = counts.setdefault(item, {'chosen_weight': 0, 'available_weight': 0})
            a['available_weight'] += row['weight']
            if item in chosen:
                a['chosen_weight'] += row['weight']
    return dict(sorted(counts.items()))
