# FlashMask: Flexible Attention Mask

In Transformer-based large model training, attention masks can introduce substantial redundant computation and make long-sequence training difficult because of their $O(N^2)$ memory footprint, where $N$ is the sequence length. Existing acceleration methods for specific mask types, such as FlashAttention, support only a limited set of attention mask patterns and do not meet the needs of flexible mask usage in large model training. To address this, PaddlePaddle introduced FlashMask, a column-wise sparse attention mask representation that supports flexible and diverse mask patterns while reducing memory complexity from $O(N^2)$ to $O(N)$. A highly efficient operator kernel is built on top of this representation to further improve training efficiency, especially for long sequences.

We evaluated FlashMask in large language model fine-tuning and alignment training on an NVIDIA A100 80G GPU, including SFT, LoRA, DPO, and RM. Compared with existing FlashAttention dense-mask methods, FlashMask improved end-to-end training speed by 1.65x to 3.22x. At the kernel level, FlashMask achieved 37.8% to 62.3% of the theoretical maximum floating-point operations, and its TFLOPs/s performance exceeded FlexAttention by 12.1% to 60.7%.

## Resources & Links

* **arXiv Paper:** [https://arxiv.org/pdf/2410.01359](https://arxiv.org/pdf/2410.01359)
* **PaddlePaddle Official Documentation:** [PaddlePaddle FlashMask Attention](https://www.paddlepaddle.org.cn/documentation/docs/en/develop/api/paddle/nn/functional/flashmask_attention_en.html)
* **PaddleNLP Open-Source Integration:** [PaddleNLP FlashMask Docs](https://github.com/PaddlePaddle/PaddleNLP/tree/develop/llm/docs/flashmask.md)
* **AI Studio Quick Experience:** [[PaddleNLP 3.0] FlashMask Flexible Attention Mask, a Sharp Tool for Long Sequence Training - PaddlePaddle AI Studio Star River Community](https://aistudio.baidu.com/projectdetail/8459413)

## Directory Breakdown

* [1. Challenges of Large Language Models](./01-llm-challenges.md)
* [2. Innovations of FlashMask: Column-wise Sparse Mask Representation and Efficient Computation](./02-flashmask-innovation.md)
* [3. Advantages of FlashMask: Dual Improvements in Speed and Storage](./03-performance-benefits.md)
* [4. Applications of FlashMask: Empowering Large Language Models](./04-applications.md)
* [5. Quick Start](./05-quick-start.md)
* [6. References](./06-references.md)
