# 论文精读笔记

## 1. 基本信息

- 标题：Swin Transformer: Hierarchical Vision Transformer using Shifted Windows
- 作者：Ze Liu、Yutong Lin、Yue Cao、Han Hu、Yixuan Wei、Zheng Zhang、Stephen Lin、Baining Guo
- 年份：未明确提及
- 会议/期刊：未明确提及

### 摘要

This paper presents a new vision Transformer, called Swin Transformer, that capably serves as a general-purpose backbone for computer vision. Challenges in adapting Transformer from language to vision arise from differences between the two domains, such as large variations in the scale of visual entities and the high resolution of pixels in images compared to words in text. To address these differences, we propose a hierarchical Transformer whose representation is computed with Shifted windows. The shifted windowing scheme brings greater efficiency by limiting self-attention computation to non-overlapping local windows while also allowing for cross-window connection. This hierarchical architecture has the flexibility to model at various scales and has linear computational complexity with respect to image size. These qualities of Swin Transformer make it compatible with a broad range of vision tasks, including image classification (87.3 top-1 accuracy on ImageNet-1K) and dense prediction tasks such as object detection (58.7 box AP and 51.1 mask AP on COCO testdev) and semantic segmentation (53.5 mIoU on ADE20K val). Its performance surpasses the previous state-of-theart by a large margin of +2.7 box AP and +2.6 mask AP on COCO, and +3.2 mIoU on ADE20K, demonstrating the potential of Transformer-based models as vision backbones. The hierarchical design and the shifted window approach also prove beneficial for all-MLP architectures. The code and models are publicly available at https://github. com/microsoft/Swin-Transformer .

## 2. 论文解决的问题

将Transformer从语言领域迁移到计算机视觉领域时，面临视觉实体尺度变化大和图像像素分辨率高导致的计算复杂度过高问题。

## 3. 背景与动机

为了构建一个能够在多种视觉任务中作为通用骨干网络的Transformer模型，同时解决上述挑战，实现高效的层次化特征表示和线性计算复杂度。

## 4. 核心方法

提出Swin Transformer，采用分层特征图构建，通过非重叠局部窗口内的自注意力计算和移位窗口机制实现跨窗口连接，使得计算复杂度与图像大小呈线性关系。

## 5. 创新点总结

- 引入分层特征图结构，适应不同尺度的视觉实体，兼容特征金字塔等密集预测技术。
- 提出移位窗口（Shifted Windows）方法，在保持高效计算的同时实现跨窗口信息交互。
- 通过循环移位和掩码机制实现高效的批处理计算，降低延迟。
- 使用相对位置偏置（Relative Position Bias）提升模型性能。

## 6. 实验设计与结果

实验在ImageNet-1K图像分类、COCO目标检测和ADE20K语义分割三个任务上进行。在ImageNet-1K上，使用top-1准确率作为评价指标，采用常规训练（300轮，AdamW优化器，余弦学习率衰减）和ImageNet-22K预训练后微调两种设置。对比基线包括DeiT、RegNet、EfficientNet等，Swin Transformer在相似复杂度下比DeiT高出1.5%左右，与RegNet和EfficientNet相比速度-精度权衡略优。ImageNet-22K预训练带来1.8%~1.9%的提升，Swin-B达到86.4% top-1，Swin-L达到87.3%。在COCO上，使用box AP和mask AP作为指标，采用Cascade Mask R-CNN等框架，对比ResNet、ResNeXt和DeiT。Swin-T比ResNet-50高3.4~4.2 box AP，比DeiT-S高2.5 box AP和2.3 mask AP。最佳模型在test-dev上达到58.7 box AP和51.1 mask AP，超越之前最佳。在ADE20K上，使用mIoU作为指标，基于UperNet框架。Swin-S比DeiT-S高5.3 mIoU，比ResNet-101高4.4 mIoU，比ResNeSt-101高2.4 mIoU。Swin-L达到53.5 mIoU，超越之前最佳3.2 mIoU。消融实验显示：移动窗口比单窗口提升1.1% top-1、2.8 box AP、2.2 mask AP、2.8 mIoU；相对位置偏置比无位置编码或绝对位置编码效果好；循环实现比朴素填充速度快13%~18%；移动窗口自注意力比滑动窗口快数倍，且精度相当。主要结论：Swin Transformer在下游任务上达到最优，具有线性计算复杂度，并验证了移动窗口和相对位置偏置的有效性。

## 7. 方法优点

提出Swin Transformer，采用分层特征图构建，通过非重叠局部窗口内的自注意力计算和移位窗口机制实现跨窗口连接，使得计算复杂度与图像大小呈线性关系。

## 8. 方法局限

- 未明确提及

## 9. 对我研究的启发

- 分层设计和移位窗口方法可推广至全MLP架构。
- 统一的视觉-语言架构有助于联合建模和知识共享。
- 局部窗口自注意力与移位窗口结合为高效处理高分辨率图像提供新思路。
