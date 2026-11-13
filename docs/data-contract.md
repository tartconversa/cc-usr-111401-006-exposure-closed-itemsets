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
