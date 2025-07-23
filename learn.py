# scripts/learn.py
#감성분류(긍정/부정)모델 생성기
import os
import json
import pandas as pd
from sklearn.model_selection import train_test_split
from datasets import Dataset
import evaluate
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    TrainingArguments,
    Trainer as HfTrainer
)

# ── 프로젝트 루트 및 data‑files 경로 설정 ───────────────────────────
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))   # …/crawl/scripts
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))                  # …/crawl
DATA_DIR        = os.path.join(PROJECT_ROOT, "data-files")     # …/crawl/data‑files
# 모델이 data-files 폴더에 저장되도록 경로 변경
SAVED_MODEL_DIR = os.path.join(DATA_DIR, "saved_model")

# 1. 레이블 달린 데이터 로드
labeled_path = os.path.join(DATA_DIR, 'labeled_news.csv')
df = pd.read_csv(labeled_path)
df = df.dropna(subset=['text','label']).reset_index(drop=True)
df['label'] = df['label'].str.strip().str.lower()
label_mapping = {'negative': 0, 'neutral': 1, 'positive': 2}
df = df[df['label'].isin(label_mapping)]
df['label_id'] = df['label'].map(label_mapping)

# 학습/검증 분리
df_train, df_val = train_test_split(
    df[['text','label_id']],
    test_size=0.2,
    stratify=df['label_id'],
    random_state=42
)

# 2. 토크나이저 및 모델 로드
MODEL_NAME = 'monologg/kobert'
tokenizer = AutoTokenizer.from_pretrained(
    MODEL_NAME, use_fast=True, trust_remote_code=True
)
model = AutoModelForSequenceClassification.from_pretrained(
    MODEL_NAME,
    num_labels=len(label_mapping),
    id2label={v:k for k,v in label_mapping.items()},
    label2id=label_mapping,
    trust_remote_code=True
)

# 3. 데이터셋 토크나이징

def tokenize_fn(batch):
    return tokenizer(
        batch['text'],
        padding='max_length',
        truncation=True,
        max_length=256
    )

train_ds = Dataset.from_pandas(df_train.reset_index(drop=True))
val_ds   = Dataset.from_pandas(df_val.reset_index(drop=True))

train_ds = train_ds.map(
    tokenize_fn, batched=True, remove_columns=['text']
).rename_column('label_id', 'labels').with_format(
    type='torch', columns=['input_ids','attention_mask','labels']
)

val_ds = val_ds.map(
    tokenize_fn, batched=True, remove_columns=['text']
).rename_column('label_id', 'labels').with_format(
    type='torch', columns=['input_ids','attention_mask','labels']
)

dataset = {'train': train_ds, 'validation': val_ds}

# 4. Trainer 설정 및 F1 메트릭
evaluation_metric = evaluate.load('f1')

def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = logits.argmax(axis=-1)
    f1 = evaluation_metric.compute(
        predictions=preds,
        references=labels,
        average='weighted'
    )['f1']
    return {'f1': f1}

training_args = TrainingArguments(
    output_dir='outputs',
    eval_strategy='steps',
    eval_steps=500,
    save_steps=500,
    per_device_train_batch_size=16,
    per_device_eval_batch_size=16,
    num_train_epochs=3,
    learning_rate=2e-5,
)

trainer = HfTrainer(
    model=model,
    args=training_args,
    train_dataset=dataset['train'],
    eval_dataset=dataset['validation'],
    compute_metrics=compute_metrics
)

# 5. 학습 및 저장
if __name__ == '__main__':
    print('✅ Training start')
    trainer.train()

    # 모델 저장
    os.makedirs(SAVED_MODEL_DIR, exist_ok=True)
    trainer.save_model(SAVED_MODEL_DIR)
    try:
        # 일반적인 HuggingFace 토크나이저 저장
        tokenizer.save_pretrained(SAVED_MODEL_DIR)
    except TypeError:
        # KoBertTokenizer의 save_vocabulary 에 맞춰서 vocab만 저장
        tokenizer.save_vocabulary(SAVED_MODEL_DIR)

    # tokenizer_config.json 저장
    config = {
        'model_max_length': tokenizer.model_max_length,
        'do_lower_case': getattr(tokenizer, 'do_lower_case', False)
    }
    with open(os.path.join(SAVED_MODEL_DIR, 'tokenizer_config.json'), 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f'✅ Model saved to {SAVED_MODEL_DIR}')