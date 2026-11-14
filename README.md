# 乡村业态共访的机会域关联发现与封闭模式索引

本项目在现有修订快照与业务摘要之上，提供面向任意业态组合的机会域关联分析与封闭模式检索。结果描述观察到的相关关系，不表示因果，也不预设哪种组合适合联营。

app/catalog.py 按集合和记录键解析修订，拒绝同修订冲突；app/baseline.py 承载单业态业务计算；app/service.py 输出摘要及输入摘要哈希。
app/association.py 构建机会域倒排索引（业态→开放/参与会话表），以有序表交集计算任意组合的共同机会域，Eclat 枚举加按支持会话身份集合的闭包合并产出封闭组合，不展开幂集；app/store.py 按 input_digest 分版本持久化查询，跨版本读取拒绝、重算后清除旧闭包；app/explore.py 是命令行入口。
数据字段、机会域/同域分母口径、分层判定与版本语义见 [资料契约](docs/data-contract.md)。fixtures/sample.json 是合成小数据，不对应真实村庄或个人。
运行环境为 Python 3.14，全部模块仅使用标准库。

读取并计算当前摘要：

    python -m app.service fixtures/sample.json

机会域与封闭模式查询：

    python -m app.explore combo  fixtures/sample.json --items 工坊,茶室
    python -m app.explore rule   fixtures/sample.json --antecedent 工坊 --consequent 茶室
    python -m app.explore closed fixtures/sample.json --min-weight 1
    python -m app.explore closed fixtures/sample.json --min-rate 0.5

在明确资料版本上重算并保存查询（修订后旧版本闭包整体清除）：

    python -m app.explore rebuild fixtures/sample.json --store .querystore \
        --spec fixtures/query-specs.example.json
    python -m app.explore list   --store .querystore
    python -m app.explore saved  --store .querystore --name closed-weight-1 --digest <input_digest>

关键口径：组合只在全部业态同时开放的会话中计算支持率；规则的前件、后件与联合指标共用 D(前件∪后件) 同一机会域，独立基线为该域内两个边际率的乘积；封闭性按未加权支持会话身份集合判断，权重相同而支持者不同不会合并。

运行当前模块检查：

    python -m unittest discover -s tests -v

检查源码编译：

    python -m compileall -q app tests
