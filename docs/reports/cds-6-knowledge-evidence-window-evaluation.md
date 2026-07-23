# CDS.6 Knowledge EvidenceWindow 评测报告

> 评测数据：纯合成；不含用户数据。

## 范围

复用现有 KnowledgeResult、知识搜索、切片、引用、传输授权与 CTX 最终装配接口；仅评测 EvidenceWindow 原子预算、完整 JSON 和未授权私密资料远传。未定义 KIG RetrievalBundle，未进入 CDS.7。

## 三指标

| 指标 | 分母 | 结果 | 完成门 |
|---|---:|---:|---:|
| 正确切片因过大而全部跳过率 | 1 | 0.0% | 0 |
| 知识 JSON 非完整率 | 2 | 0.0% | 0 |
| 未授权私密资料远传率 | 1 | 0.0% | 0 |

门禁失败原因：无

## 运行证据

- 一次性授权：pending → consumed
- 授权审计事件：preflight_created, grant_issued, grant_consumed
- 受控 Provider 边界：授权请求调用 1 次，捕获 2 条 messages；知识 JSON 完整=true；含授权私密正文=true；含明文授权码=false
- 无 grant 真实 `/api/chat`：HTTP 409 / knowledge_grant_required；Provider 边界调用=0；边界私密正文=0
- 关键样本：oversized_correct_window, atomic_private_authorization, real_api_chat_without_grant

完成门：**PASS**
