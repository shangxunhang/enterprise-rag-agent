# Word Parser Backend

`document_ingestion` 现在包含统一文档路由和两个独立 Parser Backend：`.doc/.docx` 进入 Microsoft Word COM Parser，`.pdf` 进入原有的 MinerU 主解析、质量评测、PaddleOCR fallback 流程。两条链都输出供 7 节点中台继续清洗的**解析器原始结果**；这里不输出 `cleaned_text_unit_v1`，不切 Chunk，不做 Embedding，也不写入 RAG/向量库。

## 统一文档路由

统一入口是 `document_ingestion/router_cli.py`。路由层只根据扩展名调度，不把 Word 和 PDF 解析逻辑混成一个 Parser：

```text
raw_data/
├─ *.doc / *.docx ─→ Word COM Parser
└─ *.pdf          ─→ MinerU → parse_eval → PaddleOCR fallback
                         ↓
output_root/
├─ word/               # Word 原始解析结果、表格、normalized、报告
├─ pdf/                # MinerU/PaddleOCR 产物、parsed_jsonl、profile、eval
├─ identities/         # 跨 backend 的 doc_id → source SHA-256 投影
├─ document_manifest.jsonl
└─ reports/document_ingestion_report.json
```

路由默认使用当前 Python 运行 Word；PDF 可以用 `--pdf-python` 指定原 OCR 项目的独立环境。这样 MinerU、PyMuPDF、PaddleOCR 等重依赖不会污染 Word/RAG 主环境。当前 `enterprise-rag-agent` 环境未安装这些 PDF 依赖，因此真正解析 PDF 时必须提供可用的 PDF Python 环境；`--dry-run` 不需要这些依赖。

PyCharm 可直接使用 Script path：

```text
D:\MyCode\rag-agent\document_ingestion\router_cli.py
```

完整批次参数示例：

```text
--input-dir D:\MyCode\rag-agent\raw_data --output-dir D:\MyCode\rag-agent\data\parsed\document_full_v1 --document-config D:\MyCode\rag-agent\document_ingestion\configs\document_full_v1.document-config.json --pdf-python D:\mysoftware\anaconda\envs\rag-template\python.exe --mineru-cmd D:\mysoftware\anaconda\envs\rag-template\Scripts\mineru.exe
```

只检查路由和治理配置时追加 `--dry-run`。本机已验证 `rag-template` 环境同时包含 MinerU、PyMuPDF 和 PaddleOCR；由于直接执行时该环境未必把 `Scripts` 加入 PATH，所以示例显式传入 `mineru.exe`。原始 PDF 永远不会被重命名、覆盖或移动。

## 与现有 OCR/MinerU 契约的关系

实现逐行读取并统计了 `contracts/doc_001_native_text.mineru.parsed.jsonl`。Word 输出继续使用完全相同的 20 个顶层字段；Word 特有信息只写入 `extra`。为兼容现有下游，序列化值仍为 `schema_version = parsed_pdf_unit_v1`，尽管输入不是 PDF。未来通用 Schema 升级应由中台单独迁移。

每行是一个 JSON 对象且代表一个内容单元，不存在顶层数组或文档 envelope。Word 固定字段为：

- `parser = word`
- `parse_version = word_adapter_v0.1`
- `ocr_used = false`
- `source_format` 保留原始输入的 `doc` 或 `docx`
- `block_id == parse_unit_id`
- `text_length == len(text)`

默认 `doc_id` 是 `doc_` 加源文件 SHA-256 前 24 位；也可以用 `--document-config` 明确指定。`parse_unit_id` 是 `<doc_id>_pNNNN_uNNNN_<content_type>`，页码和页内顺序均补零，因此重复解析身份稳定。每个 `doc_id` 在任何正式产物写入前都会原子发布 `identities/<doc_id>.source_identity.json`，永久绑定源 SHA-256 和原始格式。同一来源二次运行返回 `skipped_unchanged`，不会重写 parsed/table/image/normalized 产物；不同 SHA 复用同一 `doc_id`、或正式 parsed 已存在但 identity 缺失时均 fail closed。

## Word COM 安全约束

每个文件使用一个私有的 `DispatchEx("Word.Application")` 实例并串行处理。实例设置 `Visible = False`、`DisplayAlerts = 0`、强制禁用宏，并通过 `Options.UpdateLinksAtOpen = False` 禁止打开时更新链接。文档以 `ReadOnly=True`、`AddToRecentFiles=False`、`ConfirmConversions=False`、`Revert=False` 打开。所有 Document 都在 `finally` 语义下关闭，解析器会在关闭 Document 前主动释放 Range/Table/Shape/OMath 子代理；随后先释放 Application COM proxy，再向本程序独占的 Word HWND 发送正常 `WM_CLOSE`。这避免部分 Office/pywin32 组合在 `Application.Quit` 内触发 RPC first-chance exception，同时不会终止或操作用户已有的 WINWORD 进程。没有可验证私有 HWND 的 fake/mock 路径才回退到 `Quit(0)`。

`.doc` 会先只读打开，再用 `SaveAs2(..., FileFormat=16)` 写入 `normalized/<doc_id>.docx`。源文件永不覆盖，`raw_data` 内不会写入任何内容。生成物必须是非空、无损坏且包含 `[Content_Types].xml` 与 `word/document.xml` 的 DOCX ZIP。旁路 normalization 元数据记录源 SHA-256；只有 SHA 完全相同才复用已有 normalized 文件，冲突会明确失败。

## 页码、顺序和内容类型

页码来自 Microsoft Word 排版后的 Range 信息：先用 `Document.GoTo(wdGoToPage)` 取得每个真实分页起点，并在对应 Range 调用 `Information(wdActiveEndPageNumber)` 校验物理页码，再按这些 Word 返回的边界定位内容单元；受保护的老文档若不支持 GoTo，则逐单元调用 Range.Information。这里没有字符数或段落数估算。跨页单元以起始页作为 `page_no`，结束页写到 `extra.page_end`。Word 无法提供大于等于 1 的页码时，文件以 `page_number_unavailable`/`quality_rejected` 失败，绝不猜为第 1 页。

正文段落、表格、InlineShape、浮动 Shape 锚点、OMath 和 Chart 统一按 `Range.Start` 排序；表格内段落不会再作为普通段落重复输出。页眉、正文、页脚使用明确的 story 顺序，页眉页脚不会在 Parser 阶段去重。标题优先依据 `OutlineLevel`，其次识别明确的 `Heading N`/`标题 N` 样式；列表依据 Word `ListFormat`。

在序列化前统一清理 Word 专有控制字符：自动题注中的 ASCII 30（例如 `图5\x1e3`）规范为可见的 `图5-3`，Word 回车/软换行规范为 `\n`，单元格结束符和其他不可见 C0 控制字符被移除。发生过的操作写入内容单元的 `extra.text_normalizations`，因此清洗可审计，原始 JSONL 不再把隐藏控制字符传入 Spark、Chunk 或 Embedding。

表格输出 `table_block`，检索文本使用 Markdown，同时写 `tables/<block_id>.json`，包含 rows、行列数、合并/缺失网格标记和 caption。结构无法可靠表示时增加 `table_structure_degraded`。Chart 尝试导出到 `images/<block_id>.png`。普通 Word 图片缺少稳定的无交互逐对象导出 API 时仍输出 `image_block`，`image_uri = null` 并标记 `asset_not_extracted`，不会静默丢弃。OMath 输出 `formula_block` 和 Word 当前线性显示文本；不伪造 LaTeX。

## CLI

```powershell
& 'D:\mysoftware\anaconda\envs\enterprise-rag-agent\python.exe' `
  -m document_ingestion.cli `
  --input-dir 'D:\MyCode\rag-agent\raw_data' `
  --output-dir 'D:\MyCode\rag-agent\data\parsed\word_full_v2' `
  --document-config 'D:\MyCode\rag-agent\document_ingestion\configs\word_full_v2.document-config.json' `
  --dry-run
```

去掉 `--dry-run` 才会启动 Word 和写输出。CLI 会实时在 stderr 输出 `[当前/总数]`、hash、DOC 转换、解析、校验、发布、最终状态及耗时，不再等整个批次结束才显示进度。`--include '<exact filename>'` 可以重复使用，用于只处理指定 smoke 文件。`--document-config config.json` 接受单对象、对象数组或 `{ "documents": [...] }`；治理字段只进入 manifest，避免每个正文单元重复携带。

仓库内的 `configs/word_full_v2.document-config.json` 已将 `05.陕西省大件运输许可相关系统升级项目实施方案0702.doc` 标记为 `gold_reference`、`is_gold_reference=true`、`should_index=false`。该文件可以进入 HDFS 和评测数据流，但后续索引任务必须依据 manifest 跳过它。

```text
word/
├── identities/<doc_id>.source_identity.json
├── normalized/
├── parsed/<doc_id>.word.parsed.jsonl
├── images/
├── tables/
├── reports/ingestion_report.json
├── reports/ingestion_report.jsonl
└── manifest.jsonl
```

所有正式 JSON/JSONL 先写同目录临时文件，执行 flush/fsync 后再原子 replace。子路径经过 containment、Windows 保留名、遍历、盘符、ADS 冒号与尾部点/空格校验。

## 质量规则和限制

单元质量分数和文档质量门完全由确定性规则计算。当前标志包括 `short_text`、`empty_text`、`suspected_garbled`、`control_characters`、`asset_not_extracted`、`table_structure_degraded`、`formula_structure_degraded`、`page_number_unavailable`、`conversion_failed`、`parse_failed`、`insufficient_extracted_text` 和 `quality_rejected`。文档级拒绝阈值为：替换字符至少 2 个且占全部文本不低于 1%；非法控制字符至少 2 个且占比不低于 0.5%；至少 2 个乱码 block 且占全部 block 不低于 20%；至少 3 个空可检索 block 且占可检索 block 不低于 50%；大于等于 100 KB 的源文件在无有效表格/公式结构时可检索正文少于 100 字；或全文没有可检索文本且图片/图表资产全部未提取。短而合法的文档不会只因字符少而失败。

已知限制：浮动对象只按 Word 锚点参与顺序；页眉页脚按 Section story 输出而不是人为展开成每页副本；普通图片当前可能只能记录而不能逐对象导出；合并复杂表格在 COM 无法还原完整网格时会明确降级。输出目录应由现有上传流程发送到 7 节点，中台负责去重、清洗、Chunk 与后续索引。
