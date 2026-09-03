# PaperPilot Memory

## 已分析论文列表
- DEIM: DETR with Improved Matching for Fast Convergence（arXiv:2412.04234v3，Huang 等）。方法：Dense O2O（借 Mosaic/Mixup 增加每图目标数以扩大正样本，不改 O2O 结构）+ MAL 匹配感知损失（VFL 变体，target 用 IoU^γ，γ=1.5 最优，保留低质量匹配梯度）。纯训练侧改进，零推理开销。COCO：RT-DETRv2 半训练量 AP+0.5/+0.9，DEIM-D-FINE-L 54.7%AP@124FPS，D-FINE-X 56.5%AP@78FPS；CrowdHuman 密集场景 +1.5AP、小目标 APs 提升显著。与用户小目标检测/菌落计数方向相关，MAL 可迁移到检测头训练。

<!-- 格式预留：
- 标题：
  - paper_id：
  - 主题：
  - 方法：
  - 创新点：
  - 实验结论：
-->

## 用户关注的研究主题

- agent 开发
- multi-agent
- LLM
- RAG
- tool use

## 跨论文发现的规律

<!-- 初始为空，后续在完成多篇论文分析后补充。 -->
