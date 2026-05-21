# 论文精读笔记

## 1. 基本信息

- 标题：Swin Transformer: Hierarchical Vision Transformer using Shifted Windows
- 作者：Ze Liu、Yutong Lin、Yue Cao、Han Hu、Yixuan Wei、Zheng Zhang、Stephen Lin、Baining Guo
- 年份：2021
- 会议/期刊：2021 IEEE/CVF International Conference on Computer Vision (ICCV)

### 摘要

This paper presents a new vision Transformer, called Swin Transformer, that capably serves as a general-purpose backbone for computer vision. Challenges in adapting Transformer from language to vision arise from differences between the two domains, such as large variations in the scale of visual entities and the high resolution of pixels in images compared to words in text. To address these differences, we propose a hierarchical Transformer whose representation is computed with Shifted windows. The shifted windowing scheme brings greater efficiency by limiting self-attention computation to non-overlapping local windows while also allowing for cross-window connection. This hierarchical architecture has the flexibility to model at various scales and has linear computational complexity with respect to image size. These qualities of Swin Transformer make it compatible with a broad range of vision tasks, including image classification (87.3 top-1 accuracy on ImageNet-1K) and dense prediction tasks such as object detection (58.7 box AP and 51.1 mask AP on COCO testdev) and semantic segmentation (53.5 mIoU on ADE20K val). Its performance surpasses the previous state-of-theart by a large margin of +2.7 box AP and +2.6 mask AP on COCO, and +3.2 mIoU on ADE20K, demonstrating the potential of Transformer-based models as vision backbones. The hierarchical design and the shifted window approach also prove beneficial for all-MLP architectures. The code and models are publicly available at https://github. com/microsoft/Swin-Transformer .

## 2. 论文解决的问题

将Transformer从语言领域迁移到视觉领域面临的挑战，包括视觉实体尺度变化大、图像像素分辨率高导致计算复杂度高，以及现有Transformer模型产生单分辨率特征图且计算复杂度与图像大小成二次方。

## 3. 背景与动机

使Transformer能够作为计算机视觉的通用骨干网络，解决上述挑战，构建层次化特征图并实现线性计算复杂度。

## 4. 核心方法

提出Swin Transformer，通过移动窗口（shifted windows）限制自注意力计算在非重叠局部窗口内，同时允许跨窗口连接；采用层次化结构，从小尺寸patch开始逐步合并相邻patch，生成多尺度特征图；通过循环移位实现高效批处理。

## 5. 创新点总结

- 移动窗口方案在相邻层间交替窗口划分，实现跨窗口连接
- 层次化特征图设计，兼容FPN、U-Net等密集预测技术
- 局部窗口自注意力线性复杂度
- 相对位置偏置

## 6. 实验设计与结果

实验在ImageNet-1K图像分类、COCO目标检测和ADE20K语义分割三个任务上进行。分类任务使用ImageNet-1K（128万训练图，5万验证图，1000类），报告单裁剪top-1准确率。训练设置包括常规ImageNet-1K训练（AdamW优化器，300epoch，余弦学习率衰减，batch size 1024，初始学习率0.001，权重衰减0.05）和ImageNet-22K预训练后微调（90epoch预训练，30epoch微调）。目标检测使用COCO 2017（118K训练，5K验证，20K test-dev），采用Cascade Mask R-CNN、ATSS、RepPoints v2、Sparse RCNN等框架，使用AdamW优化器，3x schedule（36 epoch）。语义分割使用ADE20K（20K训练，2K验证，3K测试），框架为UperNet。评价指标：分类- top-1准确率；检测- box AP、mask AP；分割- mIoU。对比基线：分类- RegNet、EfficientNet、ViT、DeiT；检测- ResNe(X)t、DeiT；分割- ResNet-101、ResNeSt-101、SETR等。消融实验：移位窗口（+1.1% top-1，+2.8 box AP，+2.8 mIoU）、相对位置偏置（+1.2% top-1，+1.3 box AP，+2.3 mIoU）、不同自注意力方法（窗口方法比滑动窗口快4倍，与Performer相比速度略快且准确率+2.3%）。主要结论：Swin Transformer在三个任务上均达到最优或相当性能，优于之前的最优方法，尤其在密集预测任务上表现突出。

## 7. 方法优点

提出Swin Transformer，通过移动窗口（shifted windows）限制自注意力计算在非重叠局部窗口内，同时允许跨窗口连接；采用层次化结构，从小尺寸patch开始逐步合并相邻patch，生成多尺度特征图；通过循环移位实现高效批处理。

## 8. 方法局限

- 暂无

## 9. 对我研究的启发

- 层次化特征图可用于多尺度建模
- 移动窗口平衡效率与跨窗口连接
- 相对位置偏置提升性能
- 统一视觉和语言架构的潜力
