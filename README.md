# 流动电影放映公益场次采录台

给偏远村镇公共文化团队使用的离线账本。它用巡演设备日志、场记、签到和供电记录重建放映，用当期名册和场地服务范围核算村级家庭触达，再按尚未覆盖的家庭预约编排补场。样本全部为合成数据，家庭、场地和来源使用代号。

## 快速运行

需要 Python 3.10+，内置 `sqlite3` 须使用 SQLite 3.25+。在本目录执行。源码可直接导入，无需安装包或访问网络。

```powershell
python -m cinema --help
python -m cinema demo
python -m unittest discover -s tests -t . -v
```

演示在内存中运行。初始 `ridge` 的两个家庭已获兑付，补场选 `a-plain`。迟到电表记录使 `s-ridge` 的可信片源从 1800 秒降到 1080 秒，冲回 `h-r1` 一户，补场改选 `b-ridge`。最后验证原件和三份账本可重算。

## 用实际 SQLite 文件走一遍

以下命令适用于空白 `cinema.sqlite`。导入命令返回收件号及文件摘要，`reconstruct`、`settle` 和 `propose` 返回各自账本中的编号。

```powershell
python -m cinema init
foreach ($feed in 'cycles','targets','roster','scope','attendance','power','slots','bookings') {
    python -m cinema import $feed "examples/$feed.csv"
}
python -m cinema import sessions examples/sessions.jsonl
python -m cinema import player examples/player.jsonl
python -m cinema reconstruct s-ridge
python -m cinema settle nov26
python -m cinema propose nov26 --from 2026-11-19T00:00:00+08:00

python -m cinema import power examples/power-late.csv
python -m cinema reconstruct s-ridge
python -m cinema settle nov26
python -m cinema propose nov26 --from 2026-11-19T00:00:00+08:00
python -m cinema commit-plan 2
python -m cinema audit
python -m cinema replay-run 1
python -m cinema replay-settlement 2
python -m cinema replay-plan 2
python -m cinema cancel-plan 2
```

数据库路径可通过全局选项 `python -m cinema --db field.sqlite init` 更换。`coverage nov26` 可直接计算当前结果，`settle nov26` 追加实际兑付差额。可用 `--cutoff 10` 查看旧收件快照。`propose` 的 `--until` 可收窄搜索范围，默认至周期结束。`eligibility` 可以单独查一户在指定时窗能否使用某场地：

```powershell
python -m cinema eligibility nov26 ridge-hall h-r1 2026-11-10T18:00:00+08:00 2026-11-10T18:30:00+08:00
```

## 裁决与状态

| 工作流 | 主要持久状态 | 使用者可观察的决定 |
| --- | --- | --- |
| 场次取证 | 场记、播放片段、供电和考勤版本，`evidence_run` | 哪场放映有足够不同片源，哪些考勤足以证明家庭在场 |
| 覆盖兑付 | 周期、家庭名册、村目标、场地协议版本，`settlement`、`entitlement_posting` | 家庭计入哪个村、由哪场证明，何时增记或冲回兑付单位 |
| 补场排程 | 候选场次、预约版本，`proposal`、`proposal_event`、`reservation` | 哪组补场能降低缺口，何时可以锁位、留下物理占用或释放 |

### 场次取证

- 场记指定时窗、播放器、影片、片长与馈线。同一设备被两份有效场记在相交时段声明时，两场均搁置，直至场记修正或撤销。
- 播放日志以 1 倍速从 `offset` 秒起算。对场记时窗与片源边界裁剪后，相同位置的重叠日志合并，歧义位置或重叠的异影片声明所占时段剔除。已证实的停电同样剔除。达到片长 80% 的**不同片源秒数**即判放映可信。片源重播可以增加现场可见时间，片源秒数按并集去重。
- 对同一馈线，电表记录直接证明停电。调度员和电工须以不同 `observer` 在同一时段佐证，重叠以外仍是提示。单个口头来源保持提示状态。
- 单条 `sign_in` 或 `field_check` 记录须与可信场次可见时间交集达到 `min(1200, 向上取整(片长秒数/2))` 秒。数条各自不足的观察不能拼为一条。现场估数保留为备注，家庭兑付只接纳达到边界的带家庭代号记录。

### 覆盖兑付

- 场次须完整落在周期内。家庭归其当期名册中的有效村，村须有有效目标；场地对该村的有效协议区间并集须连续覆盖整场。临时在其他村场地观看的家庭也可按协议归入本村。
- 一个周期内同户只兑付一单位。按场记开始时间、场次号选择首个有资格凭证，其余候补凭证仍可查。原凭证失效且候补仍有效时，归属证据转移，单位不重复。名册改归村会先冲回旧村，再增记新村。
- 目标计算 `gap = max(0, goal - distinct_households)`。目标以上新增的家庭仍可按规则获得实际触达单位，排程得分只计目标内缺口。周期撤销冲回先前权益。兑付单位用于本地核算，项目不执行打款。

### 补场排程

- 预约以家庭为席位单位，同场重复预约合并。只接纳当前有资格的家庭。候选场次须容纳所有合资格的确认预约，超载时由团队修正容量或预约后重提。
- 场地和家庭在场时段互斥。队伍和设备包还需满足转场缓冲。已锁场次持续占用场地、人员、设备及原预约的时窗；规划时用最新范围重核未来的锁定家庭是否仍能抵减缺口。已过去的锁位不作为未来覆盖。
- 在一个排程窗口、最多 12 个可用候选内比较所有可行组合。依次最大化新增达标偏远村数、加权缺口减少量（偏远村每户 2，普通村每户 1），再最小化场次数，最后用排序后的场次号确定平局。跨场重复家庭只增加一次触达。实际覆盖和预约预测分别展示。
- 预案从 `proposed` 转成 `committed` 或 `cancelled`，已提交预案也可撤销。提交要求收件号和已有锁位未改变；冲突批次整体退回。锁位一次性生效，撤销保留原预案、状态事件和已释放记录。空预案可查，无法锁位。

## 交换文件和重算

各 feed 对应一个输入格式。`examples/` 提供所有字段和样本。文件用 UTF-8，可带 BOM。JSONL 一行一条记录，CSV 须带完整表头。每行都提供 `id`、正整数 `rev` 和 `active` 或 `void`，撤销行保留原记录形状。`targets` 的 `id` 是村号，`roster` 的 `id` 是家庭号，两个 feed 均在 `cycle` 内分配身份；其他 feed 的 `id` 在该 feed 内唯一。所有时间都须带 UTC 偏移，账本使用 Unix 秒和左闭右开区间。

| 格式 | Feed |
| --- | --- |
| JSONL | `player` 设备片段、`sessions` 人工场记 |
| CSV | `attendance` 签到/实地观察/估数、`power` 供电来源 |
| CSV | `cycles` 结算周期、`targets` 村目标、`roster` 当期家庭、`scope` 服务协议 |
| CSV | `slots` 补场可用资源、`bookings` 预约 |

同文件重试幂等；同版本同内容可带不同原始文件再次收录，首次可用证据号保持不变。迟到的低版本不覆盖高版本。同版本异值拒收整份，收件号与原件和行号一起保存。重算按记录中的收件号重选版本、复核原件摘要和行内容，并对裁决、兑付行或锁位做逐项核验。更正追加收件与新的快照，既有账本仍可按原收件号重放。普通 CLI 业务错误返回状态码 2，写操作在单个 SQLite 写事务里全成或全退。

场记、名册和来源代号由本地团队管理，收件摘要用于检查导入之后的一致性。团队负责保管 SQLite 文件和原始家庭代号映射。首版将预约视为故障重排前的集体承诺，转场秒数由现场提供，全县大规模调度可按时间窗组织输入。
