# 第三方资源与授权说明

## Live2D Cubism Core 运行时
- 文件：`frontend/public/libs/live2dcubismcore.min.js`（Cubism SDK Core 5.1.0）
- 版权：© Live2D Inc.
- 使用受 **Live2D Proprietary Software License Agreement** 与 **Live2D Cubism Core 再分发条款** 约束。
- 发布前须确认目标发行规模是否适用免费授权，或办理相应的出版许可（Publication License）。
- 参考：https://www.live2d.com/en/sdk/license/

## Live2D 模型（当前为用户提供的自用模型）
- 目录：`frontend/public/models/xiadie/`，入口 `Xiadie.model3.json`（Cubism 4）。
- 当前内置的是用户提供的"遐蝶"桌宠模型（源自一个 BongoCat MverUI 键鼠桌宠的"遐蝶"皮肤，standard 模式模型）。角色遐蝶为《崩坏：星穹铁道》角色，属**同人二次创作**素材。

> ⚠️ **授权限制（重要）**：原素材作者声明"**桌宠仅供自用，禁止二次售卖、分享、上传、二改、物料印制**"。
> 这是**个人自用**授权，**禁止再分发 / 商用 / 上传 / 二改**。
>
> - 仅可用于当前**本地个人开发 / 自用**。
> - **不可**随正式版对外分发、打包发布或上传公开仓库。
> - 这与需求文档第 9 节直接冲突——第 9 节要求"授权清晰、明确允许随桌面应用打包和再分发"，且"不得使用未经授权的同人模型"。
>
> **正式对外发布前，仍须替换为原创或已获商用/再分发授权的角色模型。** 因"禁止上传"约束，
> `.gitignore` 已排除 `frontend/public/models/`，模型不进版本库。详见该目录下 `NOTICE.md`。

## 前端/后端依赖
各 npm、PyPI 依赖遵循其各自的开源许可证，见 `frontend/package.json`、`backend/requirements.txt`。
