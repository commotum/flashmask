# 4. Applications of FlashMask: Empowering Large Language Models

FlashMask's innovations and advantages open new possibilities for accelerating attention mechanism training in Transformer-based large models. It can be applied to a range of tasks and supports efficient training on ultra-long sequences.

## 4.1 Widely Applicable for Accelerating Downstream Training of Large Language Models

FlashMask can be applied to downstream training tasks for large language models, including SFT, LoRA, DPO, and RM. In particular, DPO and RM training data consists of question-answer pairs, so multiple answers can share the same question during training. This significantly reduces redundant computation on question tokens.

## 4.2 Supports Training with Mixed Unidirectional and Bidirectional Attention Masks

FlashMask supports multiple attention modes, including causal masks (unidirectional attention) and document masks (bidirectional attention), making it suitable for scenarios that require mixed attention. For example:

* **Global + Sliding Window Mask:** This mask combines global attention with sliding window attention, capturing both global context and local details. FlashMask can process this mixed mask efficiently to improve model performance.
* **Prefix Language Models:** When generating text, the prefix portion needs to attend to all tokens, while the remaining portion uses a causal mask, as in T5 pre-training. FlashMask can support both attention modes simultaneously, improving the training and inference efficiency of prefix language models.

## 4.3 Supports Mixed Multi-Resolution Training for Multimodal Image-Text Data

In multimodal data processing, data from different modalities may have different resolutions. Although the original text does not explicitly detail FlashMask's use in multimodal and multi-resolution training, FlashMask can in principle handle such data through different attention modes and masking strategies. Its optimization for long-sequence processing can help models learn associations across modalities more effectively. For example, in image-text matching tasks, FlashMask can help align key information in images and text more effectively.

The open-source code for FlashMask has been released on the PaddlePaddle and PaddleNLP platforms, supporting models with more than 100 billion parameters and context lengths exceeding 128K tokens. FlashMask expands the design space for attention masking and provides researchers with more room to explore new masking strategies.
