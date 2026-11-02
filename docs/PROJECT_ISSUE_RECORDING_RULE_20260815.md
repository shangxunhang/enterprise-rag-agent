# RAG-Agent 项目问题记录规则

日期：2026-08-15  
性质：Project-wide Engineering Workflow Rule

---

# 1. Mandatory Rule

从本规则生效起：

> **任何在需求分析、架构设计、代码审计、方案审计、实施、测试、E2E、Closure Audit、回归或运行过程中发现的问题，无论大小、是否阻塞、是否当场解决，都必须留下可追溯文档记录。**

禁止只在聊天上下文、临时终端输出或口头结论中解决问题而不落盘。

---

# 2. 必须记录的问题范围

包括但不限于：

```text
BLOCKER
MAJOR
MINOR
NOTE
代码 bug
测试 fixture 缺口
contract wording 歧义
architecture gap
schema gap
interface / contract gap
config 问题
dependency boundary 问题
Gate / policy 问题
E2E badcase
pre-existing failure
compatibility debt
observability gap
verification gap
scope creep risk
stop condition
```

即使问题最终证明：

```text
不需要代码修改
属于既有问题
属于 deferred backlog
属于 deterministic fix
只是文档不一致
只是 test fixture 问题
```

也必须记录。

---

# 3. 最低记录内容

每个问题至少应记录：

```text
Issue ID / 可唯一识别标题
发现时间/阶段
发现位置
客观现象
证据
影响范围
Severity
是否阻塞当前阶段
是否需要用户决策
根因
解决方案或 deferred reason
验证方式
最终状态
```

如果问题尚未解决：

```text
OPEN / BLOCKED / DEFERRED
```

如果已经解决：

```text
RESOLVED / CLOSED
```

必须保留原始发现记录，不通过覆盖历史审计来“制造通过”。

---

# 4. Deterministic Issue Rule

如果问题属于确定性问题：

```text
事实充分
没有架构方向分叉
没有用户价值取舍
没有多个互斥 contract 需要拍板
```

则允许：

```text
记录问题
→ 直接写出确定性解决方案
→ 同步 Implementation Authority / SoT
→ 实施
→ 验证
```

不要求为了流程形式重复进行多轮大审计。

但仍必须留下问题和方案记录。

---

# 5. Decision-required Issue Rule

如果问题涉及：

```text
架构方向变化
canonical authority 变化
schema / interface contract 多方案选择
scope 明显扩大
业务语义取舍
兼容策略取舍
所有权变化
```

则必须：

```text
记录问题
→ 给出方案与 trade-off
→ 明确 USER DECISION REQUIRED
→ 用户冻结后再同步 authority
```

不得由实施 Agent 擅自拍板。

---

# 6. Authority Sync Rule

问题如果改变当前阶段正式 contract、implementation plan、acceptance、verification 或 DoD：

> **除了独立 issue/audit 文档，还必须同步回当前唯一 Implementation Authority / Architecture Source of Truth。**

只写 issue 文档但不更新正式 authority，视为没有完成 contract closure。

历史 audit 保持原样作为当时 evidence。

---

# 7. Closure Rule

任何阶段关闭前必须确认：

```text
所有本阶段发现的问题均有记录
所有 BLOCKER 均 CLOSED 或有明确阶段边界决定
所有 deferred 问题有明确 backlog owner/location
Implementation Authority 与最终冻结结论一致
Verification Evidence 可追溯
```

---

# 8. 当前规则状态

```text
PROJECT ISSUE RECORDING RULE = ACTIVE
SCOPE = ALL PROJECT WORK
SEVERITY THRESHOLD = NONE
CHAT-ONLY ISSUE RESOLUTION = NOT ALLOWED
```
