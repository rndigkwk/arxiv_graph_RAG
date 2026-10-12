# 큐레이션 토픽 후보 사람 검토 시트

`evals/cases_curated.json`의 10건은 모두 `review_status = pending_human_review`입니다.
자동 규칙(원천 제목·초록에 문구가 포함되는지)으로 만든 라벨이라, **사람이 검토하기 전까지
이 케이스의 점수를 검증된 품질 지표로 제시하지 않습니다.**

## 검토 방법

후보마다 두 가지를 판단합니다.

1. **정답 ID가 질문에 실제로 적합한가** — 제목·분류를 보고 판단합니다. 부적합하면 그 ID를 빼세요.
2. **필수 답변 문구가 타당한가** — 그 문구를 반드시 포함해야 정답이라고 볼 수 있는지 봅니다.
   특정 논문의 초록 표현을 그대로 요구하고 있으면 지나치게 좁은 조건일 수 있습니다.

판단을 마치면 해당 케이스의 `review_status`를 `human_reviewed`로 바꾸고, 바꾼 내용을
`label_rationale`에 적습니다. 그 뒤에 아래 명령으로 돌립니다.

```bash
uv run python -m evals.run --mode compare --cases evals/cases_curated.json \
  --limit 10 --top-k 5 --output reports/rag_curated_compare.json
```

---

## 1. `curated-topic-graph-neural-network`

**질문** — Find arXiv papers about graph neural network.

**검색어** — `graph neural network`

**정답 ID 적합성 판단**

| 적합? | arXiv ID | 제목 | 대표 분류 |
|---|---|---|---|
| ☐ | [`2011.11288`](https://arxiv.org/abs/2011.11288) | AutoGraph: Automated Graph Neural Network | cs.LG |
| ☐ | [`1812.08434`](https://arxiv.org/abs/1812.08434) | Graph Neural Networks: A Review of Methods and Applications | cs.LG |
| ☐ | [`1904.07785`](https://arxiv.org/abs/1904.07785) | Graph Wavelet Neural Network | cs.LG |

**필수 답변 문구**

- ☐ `achieved promising results in graph analysis tasks`

**자동 라벨 근거** — 대표 arXiv 논문의 제목·초록에서 ID와 근거 문구를 확인한 자동 평가 후보. 주제 관련성과 답변 사실 라벨은 사람의 검토가 필요함.

<details><summary>원천 발췌</summary>

- `2011.11288` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part3.jsonl`  
  > AutoGraph: Automated Graph Neural Network
- `1812.08434` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > Graph Neural Networks: A Review of Methods and Applications
- `1904.07785` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part3.jsonl`  
  > Graph Wavelet Neural Network
- `2011.11288` · abstract · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part3.jsonl`  
  > achieved promising results in graph analysis tasks

</details>

**검토 결과** — 제외할 ID: (없음) / 문구 수정: (없음) / 메모:

---

## 2. `curated-topic-retrieval-augmented-generation`

**질문** — Find arXiv papers about retrieval augmented generation.

**검색어** — `retrieval augmented generation`

**정답 ID 적합성 판단**

| 적합? | arXiv ID | 제목 | 대표 분류 |
|---|---|---|---|
| ☐ | [`2506.00054`](https://arxiv.org/abs/2506.00054) | Retrieval-Augmented Generation: A Comprehensive Survey of Architectures, Enhancements, and Robustness Frontiers | cs.IR |
| ☐ | [`2305.15294`](https://arxiv.org/abs/2305.15294) | Enhancing Retrieval-Augmented Large Language Models with Iterative Retrieval-Generation Synergy | cs.CL |
| ☐ | [`2402.19473`](https://arxiv.org/abs/2402.19473) | Retrieval-Augmented Generation for AI-Generated Content: A Survey | cs.CV |

**필수 답변 문구**

- ☐ `conditioning generation on external evidence retrieved at inference time`

**자동 라벨 근거** — 대표 arXiv 논문의 제목·초록에서 ID와 근거 문구를 확인한 자동 평가 후보. 주제 관련성과 답변 사실 라벨은 사람의 검토가 필요함.

<details><summary>원천 발췌</summary>

- `2506.00054` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part7.jsonl`  
  > Retrieval-Augmented Generation: A Comprehensive Survey of Architectures, Enhancements, and Robustness Frontiers
- `2305.15294` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part2.jsonl`  
  > Enhancing Retrieval-Augmented Large Language Models with Iterative Retrieval-Generation Synergy
- `2402.19473` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > Retrieval-Augmented Generation for AI-Generated Content: A Survey
- `2506.00054` · abstract · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part7.jsonl`  
  > conditioning generation on external evidence retrieved at inference time

</details>

**검토 결과** — 제외할 ID: (없음) / 문구 수정: (없음) / 메모:

---

## 3. `curated-topic-diffusion-model`

**질문** — Find arXiv papers about diffusion model.

**검색어** — `diffusion model`

**정답 ID 적합성 판단**

| 적합? | arXiv ID | 제목 | 대표 분류 |
|---|---|---|---|
| ☐ | [`2211.15089`](https://arxiv.org/abs/2211.15089) | Continuous diffusion for categorical data | cs.CL |
| ☐ | [`2410.17891`](https://arxiv.org/abs/2410.17891) | Scaling Diffusion Language Models via Adaptation from Autoregressive Models | cs.CL |
| ☐ | [`2208.11970`](https://arxiv.org/abs/2208.11970) | Understanding Diffusion Models: A Unified Perspective | cs.LG |

**필수 답변 문구**

- ☐ `modelling categorical data with diffusion models`

**자동 라벨 근거** — 대표 arXiv 논문의 제목·초록에서 ID와 근거 문구를 확인한 자동 평가 후보. 주제 관련성과 답변 사실 라벨은 사람의 검토가 필요함.

<details><summary>원천 발췌</summary>

- `2211.15089` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part4.jsonl`  
  > Continuous diffusion for categorical data
- `2410.17891` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part2.jsonl`  
  > Scaling Diffusion Language Models via Adaptation from Autoregressive Models
- `2208.11970` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part6.jsonl`  
  > Understanding Diffusion Models: A Unified Perspective
- `2211.15089` · abstract · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part4.jsonl`  
  > modelling categorical data with diffusion models

</details>

**검토 결과** — 제외할 ID: (없음) / 문구 수정: (없음) / 메모:

---

## 4. `curated-topic-reinforcement-learning`

**질문** — Find arXiv papers about reinforcement learning.

**검색어** — `reinforcement learning`

**정답 ID 적합성 판단**

| 적합? | arXiv ID | 제목 | 대표 분류 |
|---|---|---|---|
| ☐ | [`1611.05763`](https://arxiv.org/abs/1611.05763) | Learning to reinforcement learn | cs.LG |
| ☐ | [`2002.12636`](https://arxiv.org/abs/2002.12636) | Reinforcement Learning through Active Inference | cs.LG |
| ☐ | [`1611.08944`](https://arxiv.org/abs/1611.08944) | Nonparametric General Reinforcement Learning | cs.AI |

**필수 답변 문구**

- ☐ `deep RL methods that can adapt rapidly to new tasks`

**자동 라벨 근거** — 대표 arXiv 논문의 제목·초록에서 ID와 근거 문구를 확인한 자동 평가 후보. 주제 관련성과 답변 사실 라벨은 사람의 검토가 필요함.

<details><summary>원천 발췌</summary>

- `1611.05763` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part2.jsonl`  
  > Learning to reinforcement learn
- `2002.12636` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part4.jsonl`  
  > Reinforcement Learning through Active Inference
- `1611.08944` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > Nonparametric General Reinforcement Learning
- `1611.05763` · abstract · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part2.jsonl`  
  > deep RL methods that can adapt rapidly to new tasks

</details>

**검토 결과** — 제외할 ID: (없음) / 문구 수정: (없음) / 메모:

---

## 5. `curated-topic-multimodal`

**질문** — Find arXiv papers about multimodal.

**검색어** — `multimodal`

**정답 ID 적합성 판단**

| 적합? | arXiv ID | 제목 | 대표 분류 |
|---|---|---|---|
| ☐ | [`2301.04856`](https://arxiv.org/abs/2301.04856) | Multimodal Deep Learning | cs.CL |
| ☐ | [`1705.09406`](https://arxiv.org/abs/1705.09406) | Multimodal Machine Learning: A Survey and Taxonomy | cs.LG |
| ☐ | [`2203.15332`](https://arxiv.org/abs/2203.15332) | Balanced Multimodal Learning via On-the-fly Gradient Modulation | cs.CV |

**필수 답변 문구**

- ☐ `process and relate information from multiple modalities`

**자동 라벨 근거** — 대표 arXiv 논문의 제목·초록에서 ID와 근거 문구를 확인한 자동 평가 후보. 주제 관련성과 답변 사실 라벨은 사람의 검토가 필요함.

<details><summary>원천 발췌</summary>

- `2301.04856` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part4.jsonl`  
  > Multimodal Deep Learning
- `1705.09406` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part6.jsonl`  
  > Multimodal Machine Learning: A Survey and Taxonomy
- `2203.15332` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > Balanced Multimodal Learning via On-the-fly Gradient Modulation
- `1705.09406` · abstract · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part6.jsonl`  
  > process and relate information from multiple modalities

</details>

**검토 결과** — 제외할 ID: (없음) / 문구 수정: (없음) / 메모:

---

## 6. `curated-topic-knowledge-graph`

**질문** — Find arXiv papers about knowledge graph.

**검색어** — `knowledge graph`

**정답 ID 적합성 판단**

| 적합? | arXiv ID | 제목 | 대표 분류 |
|---|---|---|---|
| ☐ | [`2003.02320`](https://arxiv.org/abs/2003.02320) | Knowledge Graphs | cs.AI |
| ☐ | [`2303.13948`](https://arxiv.org/abs/2303.13948) | Knowledge Graphs: Opportunities and Challenges | cs.AI |
| ☐ | [`2308.14217`](https://arxiv.org/abs/2308.14217) | Generations of Knowledge Graphs: The Crazy Ideas and the Business Impact | cs.DB |

**필수 답변 문구**

- ☐ `creation, enrichment, quality assessment, refinement, and publication`

**자동 라벨 근거** — 대표 arXiv 논문의 제목·초록에서 ID와 근거 문구를 확인한 자동 평가 후보. 주제 관련성과 답변 사실 라벨은 사람의 검토가 필요함.

<details><summary>원천 발췌</summary>

- `2003.02320` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > Knowledge Graphs
- `2303.13948` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > Knowledge Graphs: Opportunities and Challenges
- `2308.14217` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part3.jsonl`  
  > Generations of Knowledge Graphs: The Crazy Ideas and the Business Impact
- `2003.02320` · abstract · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > creation, enrichment, quality assessment, refinement, and publication

</details>

**검토 결과** — 제외할 ID: (없음) / 문구 수정: (없음) / 메모:

---

## 7. `curated-topic-code-generation`

**질문** — Find arXiv papers about code generation.

**검색어** — `code generation`

**정답 ID 적합성 판단**

| 적합? | arXiv ID | 제목 | 대표 분류 |
|---|---|---|---|
| ☐ | [`2203.13474`](https://arxiv.org/abs/2203.13474) | CodeGen: An Open Large Language Model for Code with Multi-Turn Program Synthesis | cs.LG |
| ☐ | [`2603.29088`](https://arxiv.org/abs/2603.29088) | WybeCoder: Verified Imperative Code Generation | cs.SE |
| ☐ | [`2405.20092`](https://arxiv.org/abs/2405.20092) | Divide-and-Conquer Meets Consensus: Unleashing the Power of Functions in Code Generation | cs.CL |

**필수 답변 문구**

- ☐ `multi-step paradigm for program synthesis`

**자동 라벨 근거** — 대표 arXiv 논문의 제목·초록에서 ID와 근거 문구를 확인한 자동 평가 후보. 주제 관련성과 답변 사실 라벨은 사람의 검토가 필요함.

<details><summary>원천 발췌</summary>

- `2203.13474` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part2.jsonl`  
  > CodeGen: An Open Large Language Model for Code with Multi-Turn Program Synthesis
- `2603.29088` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part5.jsonl`  
  > WybeCoder: Verified Imperative Code Generation
- `2405.20092` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part7.jsonl`  
  > Divide-and-Conquer Meets Consensus: Unleashing the Power of Functions in Code Generation
- `2203.13474` · abstract · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part2.jsonl`  
  > multi-step paradigm for program synthesis

</details>

**검토 결과** — 제외할 ID: (없음) / 문구 수정: (없음) / 메모:

---

## 8. `curated-topic-medical-imaging`

**질문** — Find arXiv papers about medical imaging.

**검색어** — `medical imaging`

**정답 ID 적합성 판단**

| 적합? | arXiv ID | 제목 | 대표 분류 |
|---|---|---|---|
| ☐ | [`2103.01938`](https://arxiv.org/abs/2103.01938) | Medical Imaging and Machine Learning | eess.IV |
| ☐ | [`2609.07719`](https://arxiv.org/abs/2609.07719) | A radiographic world model for clinical reasoning and evidence generation | cs.AI |
| ☐ | [`1612.07003`](https://arxiv.org/abs/1612.07003) | Image biomarker standardisation initiative | cs.CV |

**필수 답변 문구**

- ☐ `research in image acquisition, algorithms, data standardization`

**자동 라벨 근거** — 대표 arXiv 논문의 제목·초록에서 ID와 근거 문구를 확인한 자동 평가 후보. 주제 관련성과 답변 사실 라벨은 사람의 검토가 필요함.

<details><summary>원천 발췌</summary>

- `2103.01938` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part3.jsonl`  
  > Medical Imaging and Machine Learning
- `2609.07719` · title · `data/ai/arxiv_cs_recent_3months_oai_part2.jsonl`  
  > A radiographic world model for clinical reasoning and evidence generation
- `1612.07003` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part6.jsonl`  
  > Image biomarker standardisation initiative
- `2103.01938` · abstract · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part3.jsonl`  
  > research in image acquisition, algorithms, data standardization

</details>

**검토 결과** — 제외할 ID: (없음) / 문구 수정: (없음) / 메모:

---

## 9. `curated-topic-federated-learning`

**질문** — Find arXiv papers about federated learning.

**검색어** — `federated learning`

**정답 ID 적합성 판단**

| 적합? | arXiv ID | 제목 | 대표 분류 |
|---|---|---|---|
| ☐ | [`1908.07873`](https://arxiv.org/abs/1908.07873) | Federated Learning: Challenges, Methods, and Future Directions | cs.LG |
| ☐ | [`1602.05629`](https://arxiv.org/abs/1602.05629) | Communication-Efficient Learning of Deep Networks from Decentralized Data | cs.LG |
| ☐ | [`1912.04977`](https://arxiv.org/abs/1912.04977) | Advances and Open Problems in Federated Learning | cs.LG |

**필수 답변 문구**

- ☐ `training statistical models over remote devices`

**자동 라벨 근거** — 대표 arXiv 논문의 제목·초록에서 ID와 근거 문구를 확인한 자동 평가 후보. 주제 관련성과 답변 사실 라벨은 사람의 검토가 필요함.

<details><summary>원천 발췌</summary>

- `1908.07873` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > Federated Learning: Challenges, Methods, and Future Directions
- `1602.05629` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > Communication-Efficient Learning of Deep Networks from Decentralized Data
- `1912.04977` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > Advances and Open Problems in Federated Learning
- `1908.07873` · abstract · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part1.jsonl`  
  > training statistical models over remote devices

</details>

**검토 결과** — 제외할 ID: (없음) / 문구 수정: (없음) / 메모:

---

## 10. `curated-topic-AI-agents`

**질문** — Find arXiv papers about AI agents.

**검색어** — `AI agents`

**정답 ID 적합성 판단**

| 적합? | arXiv ID | 제목 | 대표 분류 |
|---|---|---|---|
| ☐ | [`2502.18359`](https://arxiv.org/abs/2502.18359) | Responsible AI Agents | cs.CY |
| ☐ | [`2505.10468`](https://arxiv.org/abs/2505.10468) | AI Agents vs. Agentic AI: A Conceptual Taxonomy, Applications and Challenges | cs.AI |
| ☐ | [`2503.12687`](https://arxiv.org/abs/2503.12687) | AI Agents: Evolution, Architecture, and Real-World Applications | cs.AI |

**필수 답변 문구**

- ☐ `modules for perception, planning, and tool use`

**자동 라벨 근거** — 대표 arXiv 논문의 제목·초록에서 ID와 근거 문구를 확인한 자동 평가 후보. 주제 관련성과 답변 사실 라벨은 사람의 검토가 필요함.

<details><summary>원천 발췌</summary>

- `2502.18359` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part6.jsonl`  
  > Responsible AI Agents
- `2505.10468` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part3.jsonl`  
  > AI Agents vs. Agentic AI: A Conceptual Taxonomy, Applications and Challenges
- `2503.12687` · title · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part2.jsonl`  
  > AI Agents: Evolution, Architecture, and Real-World Applications
- `2503.12687` · abstract · `data/ai_references/arxiv_ai_references_api/arxiv_ai_references_api_part2.jsonl`  
  > modules for perception, planning, and tool use

</details>

**검토 결과** — 제외할 ID: (없음) / 문구 수정: (없음) / 메모:

---

