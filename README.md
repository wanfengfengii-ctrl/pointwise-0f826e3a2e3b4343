# 有序观测湖

轨检资料组用这个本地归档保存巡检仪日批、归档勘误和复核交班。源位置可以回查仪器记录。固定日表上的复核选区随原选行的延续交给下一班，交班件可以逐行复算行数和缺测数。

## 运行

在项目根目录使用 Python 3.11。标准库自带的 SQLite 管理单文件归档。

```sh
python3 -B verify.py
python3 -B demo.py
python3 -B -m unittest discover -s tests -t . -v
```

`verify.py` 发现并运行整套业务回归，再运行 `signoff_examples.py verify` 的签回联合核验，任一失败时退出码为非零。`demo.py` 读取 `samples/observations.json`，在临时目录创建归档，重新打开后按四个固定请求计算交班件，并另行演示发件、签回与重开后的截止待办。两条入口结束时由临时目录上下文清理各自生成的归档。

## 一天的工作

1. 值班员登记一份有序仪器日批，随后可按批号及批内位置核对原始读数。
2. 归档员按原序冻结同日的初版日表，得到独立的修订号。
3. 资料组可重开归档，按修订号核对固定的行位置、来源与值。
4. 质控员在一个固定修订上标记一个非空半开区间。
5. 归档员从指定父版开始，依次移动、复制或删除行，得到新修订。
6. 资料组按号分别读取父版和后继，核对更正及原标记。
7. 接班人员在指定修订领取原选区中仍存续的行。
8. 离线终端读取同次领取的交班件，核对来源、坐标、读值及摘要。
9. 资料组对指定修订发件，冻结实际交付明细并取得唯一发件号。
10. 离线复核员按发件号逐行签回确认或列回待办，整件入档得到截止号。
11. 接班员按指定修订和截止号领取当时的待复核明细；重开后仍可按旧截止复查。

## Python 接口

```python
from catalog.snapshots import read_revision
from catalog.transactions import open_archive
from delivery.export import export
from intake.batches import read_batch, register_batch
from review.claims import claim
from review.tickets import mark, read_ticket
from revision.coordinates import Copy, Delete, Move
from revision.publish import edit_day, seal_day

archive = open_archive('track.sqlite3')
register_batch(archive, 'run-a', '2026-11-18', [11, 22, None, 44])
raw = read_batch(archive, 'run-a')
base = seal_day(archive, '2026-11-18', 'run-a')
ticket = mark(archive, base, 1, 3)
corrected = edit_day(archive, base, [Copy(1, 4), Move(1, 0)])
withdrawn = edit_day(archive, corrected, [Delete(0)])
old_view = read_revision(archive, base)
frozen_ticket = read_ticket(archive, ticket)
claim_rows = claim(archive, ticket, corrected).rows
payload = export(archive, ticket, withdrawn)
```

- `open_archive(path)` 初始化或打开本地文件。父目录应已存在。重复初始化保留已经登记的原批、修订和标记。
- `register_batch(archive, batch_id, day_id, readings)` 返回批号。标识是非空 UTF-8 文本，读数是有序输入中的 `int` 或 `None`。同批号的日号和有序内容均相同才视作重试。`read_batch` 返回包含日号和有序源行的不可变对象。
- `seal_day(archive, day_id, batch_id)` 按同日原批生成初版。`edit_day(archive, parent_revision, operations)` 生成指定父版的一个后继。两者返回已提交的整数修订号。`read_revision` 返回不可变修订视图，含父号、日号、批号和按位置排列的行。每行提供 `position`、`source` 和 `value`，来源提供 `batch_id` 和 `position`。
- `mark(archive, revision_id, start, stop)` 返回整数标记号。要求 `0 <= start < stop <= 行数`。`read_ticket` 返回原修订号和原区间。
- `claim(archive, ticket_id, target_revision)` 返回不可变领取结果，含标记号、目标修订号和有序明细。每行提供 `marked_position`、`target_position`、`source` 和 `value`。目标须为同版或实际后继。一个标记可多次领取。
- `export(archive, ticket_id, target_revision)` 返回完整的 UTF-8 JSON 字节，含 `ticket_id`、`target_revision`、`rows` 和 `totals`。摘要字段为 `row_count` 和 `missing_count`。交班件可以在没有 SQLite 归档的终端解析。

### 离线发件与签回

```python
from delivery.export import issue
from review.claims import pending, submit

issue_id, payload = issue(archive, ticket, corrected)
cutoff = submit(archive, 'r1', issue_id, [(0, None, 'confirmed')])
todo = pending(archive, ticket, corrected, cutoff).rows
```

- `issue(archive, ticket_id, target_revision)` 按 `claim` 规则领取全部存续原选行，冻结实际交付明细及 `target_position` 对 `marked_position` 的对应关系，返回归档内唯一的正整数发件号和离线 JSON。载荷在 V1 导出字段之外增加 `issue_id`；行、摘要、目标顺序、空件、缺测和精确整数都沿用 V1。`export` 不生成发件号。
- `submit(archive, receipt_id, issue_id, entries)` 用一条整件回执返回统一入档序号（截止）。`entries` 是非空的 `(target_position, predecessor, decision)` 序列，位置按该发件解释且不得重复，`decision` 为 `confirmed` 或 `pending`。每个位置须在该件实际交付的明细中；结论按 `(ticket_id, marked_position)` 归属到原选行，与发件和目标修订无关。每条 `predecessor` 须等于该原选行入档前的当前结论回执号，从未有结论时为 `None`。同号、同发件、同条目集合的重传（顺序无关）返回原截止，不校验当前前任；同号异内容、未发位置、前任不符都整件拒绝。新回执从一个统一序号起编号，成功时整件与截止一起提交。
- `pending(archive, ticket_id, target_revision, cutoff)` 按 `claim` 的目标资格与排序，返回截至 `cutoff` 仍存续、且没有结论或最新结论为 `pending` 的原选行。`cutoff` 为 0 表示签回前；否则须是已存在的成功入档序号。重开后仍可按旧截止查询，`claim` 本身不受签回影响。

身份与截止不变量：一个发件号固定一份冻结明细，其位置只在该件内有效；签回把目标位置解析回该件的原选行身份 `(ticket_id, marked_position)`，移位的行不因位置变化而换身份，同源的不同原选行彼此独立；截止是该原选行结论历史上的一个有序观察点，`pending(..., cutoff)` 只累计截止及以前入档的结论，早先截止的答案永不被后来的签回改写。

## 顺序、精度与错误

行号从零起算。`Copy(source, gap)` 和 `Move(source, gap)` 的第二个参数是该步起始列表的插入缝，取值从 0 到该步行数。`Delete(position)` 的位置也相对于该步开始时的列表。举例，从 `[A,B,C,D]` 复制 B 到末尾，再以 `Move(0, 3)` 移动 A，得到 `[B,C,A,D,B′]`。移动保留原出现，副本有自己的出现，删除仅移除被指定的出现。空批及空日表可存档，空日表不能登记选区，空编辑序列会冻结父版原序的后继。

读值沿用仪器整数刻度，SQLite 中保存精确整数文本。`None` 对应 JSON 的 `null`。零和负整数均为有效读数。对超过 Python 默认十进制转换长度的读值，可用 `json.loads(payload, parse_int=decimal.Decimal)` 在 Python 中精确读取交班件。明细按目标行位置排列，可非连续或为空。

参数形式、坐标、区间、批冲突和关系错误引发 `ValueError`。查找未知的有效编号或批号引发 `LookupError`。整数标记和修订编号须是 SQLite 正整数范围内的 `int`。SQLite 或提交前钩子引发的异常向调用方传播。编辑及其完整行序一起提交，失败调用无可见后继。非法领取直接抛出异常。修订与标记编号只在成功后交给调用方。发件号、回执号和截止同样只在成功提交后可见：失败发件不占用号，失败回执不产生截止。`submit` 的回执冲突、未发位置与前任不符，以及 `pending` 的目标资格错误引发 `ValueError`；未知的合法标记、修订、发件或非零截止引发 `LookupError`；`pending` 的截止本身非法（负值、布尔、浮点等）引发 `ValueError`。

## 回归

`tests/test_intake.py` 核对原序、来源、重试和重复初始化。`tests/test_revision.py` 核对编辑与标记坐标以及提交失败后的恢复。`tests/test_review.py` 核对固定选择、同源行的独立存续和目标顺序。`tests/test_delivery.py` 从实际明细独立复算交班摘要，核对固定目标、坐标、空结果和精确读值。`tests/test_signoff.py` 核对发件冻结、唯一发件号、整件签回、重传幂等、截止窗口、移位身份和重开后的旧截止查询，V1 归档可直接升级。

## 签回核验与演示

`python3 -B signoff_examples.py fixture|verify|demo` 使用与 `tests` 相同的临时归档。`fixture` 只核对 V1 存续坐标；`verify` 串联发件、签回、截止待办、拒绝与回滚重试；`demo` 打印实际发件明细、各截止的待办结果和重开后的查询。`verify.py` 同时运行整套回归与该联合核验，任一失败返回非零退出码。

