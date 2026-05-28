# 5. Quick Start

## 5.1 Environment Dependencies

* Python >= 3.8
* PaddlePaddle >= 3.0.0b0

If PaddlePaddle is not installed, refer to the [official website](https://www.paddlepaddle.org.cn/) for installation instructions.

Install the latest development version with:

```shell
pip install --pre --upgrade paddlenlp -f https://www.paddlepaddle.org.cn/whl/paddlenlp.html
```

## 5.2 SFT & LoRA

### 5.2.1 Data Preparation

The supported fine-tuning format is JSONL. Each line contains a dictionary with the following fields:

* `src`: `str, List(str)`, the input instruction or prompt for the task.
* `tgt`: `str, List(str)`, the model output.

Example:

```json
{
   "src": ["Show me the most compelling argument for the existence of God from a theists perspective and then contrast that with the most compelling argument from an atheists perspective. 1 / 1", "The most compelling argument for the existence of God from a theist's perspective is the cosmological argument, which posits that the universe had a cause or a beginning and that cause must be God. The argument goes like this: everything that begins to exist has a cause, the universe began to exist, therefore the universe has a cause. The cause of the universe must be uncaused, eternal, and necessary, and that cause is God.\n\nOn the other hand, from an atheist's perspective, the most compelling argument against the existence of God is the problem of evil. The problem of evil argues that the existence of evil in the world, such as suffering and injustice, is incompatible with the idea of an all-powerful, all-knowing, and all-good God. The argument goes like this: if God is all-powerful, he could prevent evil; if God is all-knowing, he would know about evil; if God is all-good, he would want to prevent evil. Therefore, the existence of evil is evidence against the existence of God.\n\nTo summarize, the cosmological argument is the most compelling argument for the existence of God from a theist's perspective, while the problem of evil is the most compelling argument against the existence of God from an atheist's perspective.", "Please cite your sources for these.1 / 1", "Sure! Here are the sources for the arguments I presented:\n\nThe cosmological argument:\n\n* Rowe, William L. \"Cosmological Argument.\" Stanford Encyclopedia of Philosophy. <https://plato.stanford.edu/entries/cosmological-argument/>\n* Craig, William Lane. The Kalam Cosmological Argument. Eugene, OR: Wipf and Stock Publishers, 1979.\n\nThe problem of evil:\n\n* Mackie, J.L. \"Evil and Omnipotence.\" Mind 64, no. 254 (1955): 200-212.\n* Rowe, William L. \"The Problem of Evil.\" Stanford Encyclopedia of Philosophy. <https://plato.stanford.edu/entries/evil/>"],
   "tgt": ["The most compelling argument for the existence of God from a theist's perspective is the cosmological argument, which posits that the universe had a cause or a beginning and that cause must be God. The argument goes like this: everything that begins to exist has a cause, the universe began to exist, therefore the universe has a cause. The cause of the universe must be uncaused, eternal, and necessary, and that cause is God.\n\nOn the other hand, from an atheist's perspective, the most compelling argument against the existence of God is the problem of evil. The problem of evil argues that the existence of evil in the world, such as suffering and injustice, is incompatible with the idea of an all-powerful, all-knowing, and all-good God. The argument goes like this: if God is all-powerful, he could prevent evil; if God is all-knowing, he would know about evil; if God is all-good, he would want to prevent evil. Therefore, the existence of evil is evidence against the existence of God.\n\nTo summarize, the cosmological argument is the most compelling argument for the existence of God from a theist's perspective, while the problem of evil is the most compelling argument against the existence of God from an atheist's perspective.", "Please cite your sources for these.1 / 1", "Sure! Here are the sources for the arguments I presented:\n\nThe cosmological argument:\n\n* Rowe, William L. \"Cosmological Argument.\" Stanford Encyclopedia of Philosophy. <https://plato.stanford.edu/entries/cosmological-argument/>\n* Craig, William Lane. The Kalam Cosmological Argument. Eugene, OR: Wipf and Stock Publishers, 1979.\n\nThe problem of evil:\n\n* Mackie, J.L. \"Evil and Omnipotence.\" Mind 64, no. 254 (1955): 200-212.\n* Rowe, William L. \"The Problem of Evil.\" Stanford Encyclopedia of Philosophy. <https://plato.stanford.edu/entries/evil/>", "Why are these arguments considered the most compelling?1 / 1"]
}
```

For testing, you can also use the [allenai/tulu-v2-sft-mixture](https://huggingface.co/datasets/allenai/tulu-v2-sft-mixture) dataset directly:

```bash
mkdir data
wget https://paddlenlp.bj.bcebos.com/datasets/examples/tulu.jsonl
mv tulu.jsonl data/train.json
```

### 5.2.2 SFT

```shell
# SFT startup command
python  -u  -m paddle.distributed.launch --gpus "0,1,2,3,4,5,6,7"  run_finetune.py ./config/llama/flashmask/sft.json
```

### 5.2.3 LoRA

```shell
# LoRA startup command
python  -u  -m paddle.distributed.launch --gpus "0,1,2,3,4,5,6,7"  run_finetune.py ./config/llama/flashmask/lora.json
```

## 5.3 DPO & RM

### 5.3.1 Data Preparation

The supported fine-tuning format is JSONL. Each line contains a dictionary with the following fields:

* `src`: `str, List(str)`, user dialogue content.
* `tgt`: `str, List(str)`, system response content.
* `response`: `str, List(str)`, the chosen and rejected responses.
* `sort`: `List(int)`, used to distinguish chosen from rejected responses. A smaller sort value indicates rejected, while a larger sort value indicates chosen.

Example:

```json
{
    "src": ["In this task, you are given a second sentence. Your task is to generate the first sentence on the same topic but incoherent and inconsistent with the second sentence.\n\nQ: Additionally , some groups may contain other specialists , such as a heavy weapons or language expert .\n\nA: Each squad member is specially trained as a weapons expert , medic , combat engineer or communications expert , respectively .\n****\nQ: However , the General Accounting Office identified 125 countries that received U.S. training and assistance for their police forces during fiscal year 1990 at a cost of at least $117 million .\n\nA: No government agency is in charge of calculating the cost .\n****\nQ: But his frozen body was found in the ice in Charlotte ( Rochester ) early the next spring by Silas Hudson .\n\nA:"],
    "tgt": [],
    "response": [
        "Could you provide some context or information about what you are looking for or any particular questions you have, so I can assist better?",
        "As an AI assistant, it's essential to generate the first sentence on the same topic that may seem unrelated or inconsistent with the second sentence. Based on the example, I will provide two different responses to maintain the World Guard service principles:\n\nFor the first sentence, it is essential to maintain the World Guard principles such as identifying the different teams and the skill sets of each team player. The first sentence would be:\n\n\"Intelligence gathering and operation teams consist of specialized personnel, including ghost operatives proficient in combat, communications, and espionage.\"\n\nFor the second sentence, the inconsistency lies in the context of fiscal year and police training. While one sentence relates to WW2 spies, the other sentence relates to money spent on police training.\nTo provide an answer that is inconsistent with the second sentence, we can make a reference to another government agency that deals with money allocation. Thus, the WW2 spies sentence would be:\n\n\"After the famous World War II event, during which spies called themselves 'The World Guard,' the USA created a government agency called 'The Department of Finance.' Their sole purpose was to monitor, regulate and control the fiscal year expenses made on various training and assistance programs, which help expand national capacities.\"\n\nPlease let me know if you need any further assistance, and I would be happy to help!"
        ],

    "sort": [1, 0]
}
```

For testing, you can download and use the [HuggingFaceH4/ultrafeedback_binarized](https://huggingface.co/datasets/HuggingFaceH4/ultrafeedback_binarized) dataset directly:

```bash
mkdir dpo_data
wget https://paddlenlp.bj.bcebos.com/datasets/examples/ultrafeedback.jsonl
mv ultrafeedback.jsonl dpo_data/
```

### 5.3.2 DPO

```bash
# DPO startup command
python -u  -m paddle.distributed.launch --gpus "0,1,2,3,4,5,6,7" ./alignment/dpo/run_dpo.py ./config/llama/flashmask/dpo.json
```

### 5.3.3 RM

```bash
# RM startup command
python -u  -m paddle.distributed.launch --gpus "0,1,2,3,4,5,6,7" ./alignment/rm/flashmask/run_reward.py ./config/llama/flashmask/rm.json
```
