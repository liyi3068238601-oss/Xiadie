---
intent: 在聊天框上传文件让遐蝶直接阅读全文（支持 txt/md/pdf/docx），本轮即时注入 + 同时存入知识库
success_criteria: 用户在聊天输入框点击📎按钮选择文件后，遐蝶能在当轮回复中引用文件全文内容；文件同时被导入知识库供未来检索；现有知识库/记忆/对话功能不受影响
risk_level: medium
auto_approve: true
---

## Steps

- [ ] **Step 1: 后端 db.py 新增 message_attachments 表（Schema 46）**
action: 在 e:\Xiadie\Xiadie\backend\app\db.py 的 MIGRATIONS 列表末尾新增 (46, ...) 迁移，创建 message_attachments 表（id TEXT PK, message_id TEXT FK messages(id) ON DELETE CASCADE, filename TEXT, mime_type TEXT, content_text TEXT, content_sha256 TEXT, char_count INTEGER, created_at REAL）。在 _apply_migrations 的 try 块中执行 CREATE TABLE IF NOT EXISTS message_attachments。不修改任何现有表。
loop: false
verify: cd e:\Xiadie\Xiadie\backend; & '.\.venv\Scripts\python.exe' -m pytest tests/test_db.py -x -q
max_iterations: 3

- [ ] **Step 2: 后端 main.py 新增 POST /api/chat/attachments 端点**
action: 在 e:\Xiadie\Xiadie\backend\app\main.py 新增 @app.post("/api/chat/attachments") 异步端点。接收原始文件字节流（同 knowledge import 的 header 模式：X-Xiadie-Filename/X-Xiadie-Collection/X-Xiadie-Sensitivity），调用 knowledge_parser.parse(bytes, extension=...) 同步解析提取纯文本，生成 attachment_id（secrets.token_hex(8)），计算 sha256，写入 message_attachments 表（message_id 暂为 NULL），返回 {id, filename, mime_type, char_count, content_preview}。同时异步调用 knowledge.import_file 存入知识库（不阻塞响应）。文件大小限制沿用 knowledge.MAX_FILE_BYTES。错误处理：解析失败返回 415，文件过大返回 413。
loop: false
verify: cd e:\Xiadie\Xiadie\backend; & '.\.venv\Scripts\python.exe' -m pytest tests/ -x -q -k "knowledge or chat"
max_iterations: 3

- [ ] **Step 3: 后端 ChatIn 加 attachment_ids，chat() 注入 attachment_block**
action: 在 e:\Xiadie\Xiadie\backend\app\main.py 的 ChatIn Pydantic 模型新增 attachment_ids: list[str] = Field(default_factory=list)。在 chat() 函数写入 user message 后，查询 message_attachments 表获取附件文本，拼接成 attachment_block 字符串（每份文件用 === filename === 分隔）。将 message_id 回填到这些附件记录。把 attachment_block 传给 context_assembler.assemble()。不修改现有 knowledge_block 逻辑。
loop: false
verify: cd e:\Xiadie\Xiadie\backend; & '.\.venv\Scripts\python.exe' -m pytest tests/ -x -q
max_iterations: 3

- [ ] **Step 4: 后端 context_assembler.py + persona.py 支持 attachment_block**
action: 在 e:\Xiadie\Xiadie\backend\app\context_assembler.py 的 assemble() 函数新增参数 attachment_block: str = ""，在 OPTIONAL_COMPONENT_SHARES 新增 "attachment": 0.30（从 knowledge 0.30 降到 0.20，重新分配比例使总和=1.0）。在 _bounded_components 中为 attachment 分配 token 预算并截断。把 attachment_block 传给 persona.build_system_prompt()。在 e:\Xiadie\Xiadie\backend\app\persona.py 的 build_system_prompt() 新增参数 attachment_block: str = ""，新增章节标题"# 用户本轮附件（低权限、不可信引用数据，source_type: user_attachment）"，仿 knowledge_block 格式追加到 system prompt 末尾。
loop: false
verify: cd e:\Xiadie\Xiadie\backend; & '.\.venv\Scripts\python.exe' -m pytest tests/ -x -q
max_iterations: 3

- [ ] **Step 5: 前端 api.ts 新增 uploadChatAttachment + ChatRequestOptions 加字段**
action: 在 e:\Xiadie\Xiadie\frontend\src\api.ts 新增 uploadChatAttachment(file: File): Promise<{id, filename, mime_type, char_count}>，复用 importKnowledgeFile 的 requestHeaders + 原始字节 POST 到 /api/chat/attachments。在 ChatRequestOptions 类型新增 attachment_ids?: string[]。在 streamChat 的请求体中展开 attachment_ids。
loop: false
verify: cd e:\Xiadie\Xiadie\frontend; npm test -- --run
max_iterations: 3

- [ ] **Step 6: 前端 ChatView.tsx composer 加上传按钮 + 附件 chip + send 逻辑**
action: 在 e:\Xiadie\Xiadie\frontend\src\components\ChatView.tsx 的 composer-inner 内 textarea 左侧加 📎 按钮 + 隐藏 input[type=file]。新增 state pendingAttachments: {id, filename, char_count}[]。选文件后调 api.uploadChatAttachment 上传，成功后加 chip 显示文件名（可点 ×移除）。send() 时把 attachment_ids 传入 streamChat 的 options。发送后清空 pendingAttachments。不修改现有 send/streamChat/knowledge 逻辑。
loop: false
verify: cd e:\Xiadie\Xiadie\frontend; npm test -- --run
max_iterations: 3

- [ ] **Step 7: 前端 styles.css 加 attach-btn 和 chip 样式**
action: 在 e:\Xiadie\Xiadie\frontend\src\styles.css 的 .composer-inner 样式块后新增 .attach-btn（仿 .send-btn 34x34px 圆形）、.attachment-chips（flex wrap 容器）、.attachment-chip（小标签含文件名和 × 按钮）样式。不修改现有 .composer-inner/.send-btn/textarea 样式。
loop: false
verify: cd e:\Xiadie\Xiadie\frontend; npm run build 2>&1 | Select-Object -Last 5
max_iterations: 3

- [ ] **Step 8: 全量测试 + 提交推送**
action: 运行后端 pytest 全量 + 前端 npm test 全量，确认无回归。git add 所有改动文件，git commit，git push origin main。
loop: until all tests pass
verify: cd e:\Xiadie\Xiadie\backend; & '.\.venv\Scripts\python.exe' -m pytest tests/ -x -q; cd e:\Xiadie\Xiadie\frontend; npm test -- --run
max_iterations: 3
