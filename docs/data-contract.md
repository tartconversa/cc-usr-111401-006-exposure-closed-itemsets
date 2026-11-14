# 资料与现有计算约定

## 修订快照

schema_version为整数1，metadata为业务方案字典，collections为业务集合字典。每条记录有非空字符串id和正整数revision。
同集合相同id取最高revision；同id同revision内容不同即冲突，即便存在更高修订也拒绝。deleted=true为撤回，之后更高修订可恢复。
撤回记录不要求携带业务字段。输入顺序与相同记录重试不改变语义；非有限数字拒绝。
Snapshot.rows返回独立副本；input_digest绑定有效修订和全部metadata。输出data_version沿用metadata，不由程序自造资料版本。
既有摘要入口为app.service.inspect(payload)，输出schema_version、input_digest、data_version和summary。

## 业务资料

sessions 含 visitor、stratum、available、chosen、weight。chosen 必须是available子集，集合内重复键折叠。每条会话一个权重，外部已处理游客抽样，不在会话内再按选择项数分摊。baseline.analyze 输出单业态的加权参与数与开放数，不生成组合。封闭性使用未加权的支持会话身份集合；门槛口径由查询声明为支持总权重或机会域支持率。

各集合必要字段：{"sessions": ["visitor", "stratum", "available", "chosen", "weight"]}。

## 当前能力

app.baseline.analyze(snapshot)产生已有摘要，app.service.inspect负责绑定输入身份；新增分析可以增设独立入口，已有接口及上述数据语义保持兼容。

## 机会域关联与封闭模式（app.association / app.store / app.explore）

### 机会域与同域分母

组合 X 的机会域 D(X) 是 X 内全部业态同时开放的会话有序集合，由开放倒排表逐表归并交集得到，不展开幂集。组合支持率只在 D(X) 内计算；从未同时开放的组合得到空机会域（estimable=false），与“开放但无人参与”（非空域、支持率为 0）明确区分。

规则 A→B 的全部指标使用同一机会域 D(A∪B)：前件参与数、后件参与数、联合参与数都先与该域相交后再统计。独立基线是该域内两个边际参与率的乘积 p(A)·p(B)（同分母），不使用各业态在自身开放分母上的比例相乘；lift、leverage、confidence 均基于该同域口径。游客权重按会话单一权重处理，不按选择项数分摊。

### 封闭性

封闭性按完整资料中未加权的支持会话身份集合是否相等判断：支持 tidset 相同的达标节点合并为一个闭包，加权支持数相同但支持者身份不同的 tidset 不会合并。挖掘采用 Eclat 后缀枚举（交集即时剪枝）加按精确 tidset 后置合并；min_weight 为反单调门槛，min_rate 非反单调，只剪零支持分支并受 max_nodes 保护，截断时 truncated=true 且 closure_guarantee 明确声明不保证封闭完整性。search_stats 报告根业态数、访问节点、相交计算次数、零支持/权重剪枝数、tidset 合并数、前沿峰值与 index_memory_bytes，作为稀疏长尾上的实际候选规模与内存证据。

### 分层解释

rule_report 输出每个 stratum 的同域指标，并给出机器可读判定：single_stratum_support（共同参与仅出现在一个分层）、composition_effect（总体 lift 高于任一分层内 lift，相关来自分层混合而非层内共选）、consistent_across_strata、mixed_or_insufficient、no_support。所有输出描述观察到的相关，不表示因果，也不预设任何组合适合联营。

### 版本化持久化

AssociationIndex 与每个保存结果都携带 input_digest。app.store.QueryStore 按 root/<digest>/<query>.json 分版本存放；load 只在指定 digest 目录内读取，版本不符抛 StaleVersionError。rebuild 在新快照版本上重算全部查询规格并整体删除其他 digest 目录，撤回或修订开放记录后不残留旧闭包。
