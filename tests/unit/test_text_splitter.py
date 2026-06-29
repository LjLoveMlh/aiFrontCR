"""切片器单测（不依赖外部服务）."""

from __future__ import annotations

from app.services.text_splitter import (
    split_generic,
    split_markdown_spec,
    split_review_case,
)


SAMPLE_SPEC = """# 测试规范

## 规则 RULE-001：禁止使用 any

### 级别
禁止

### 反例
```typescript
const x: any = 1;
```

### 正例
```typescript
const x: number = 1;
```

---

## 规则 RULE-002：函数长度不超过 50 行

### 级别
建议

### 说明
超过 50 行应拆分。
"""


def test_split_markdown_spec():
    docs = split_markdown_spec(SAMPLE_SPEC, "doc-1", {"title": "test"})
    assert len(docs) >= 2
    # 至少包含一个 H2 标题
    h2s = [d.metadata.get("h2", "") for d in docs]
    assert any("RULE-001" in h for h in h2s)
    assert any("RULE-002" in h for h in h2s)
    # chunk_index 单调
    for i, d in enumerate(docs):
        assert d.metadata.get("chunk_index") == i


SAMPLE_REVIEW = """# 评审记录 PR-001

## 评审点 1 - 头像缺少 onError

### 文件
src/components/UserAvatar.tsx

### 代码（错误）
```tsx
<img src={src} />
```

### 代码（正确）
```tsx
<img src={src} onError={onError} alt={alt} />
```

### 评审意见
必须加 onError 和 alt。

### 级别
必须

---

## 评审点 2 - 缺少 src 兜底

### 文件
src/components/UserAvatar.tsx

### 代码（错误）
```tsx
<img src={src} />
```

### 评审意见
加载失败回退默认头像。

### 级别
建议
"""


def test_split_review_case():
    docs = split_review_case(SAMPLE_REVIEW, "doc-2", {"title": "review"})
    assert len(docs) == 2
    assert "头像缺少 onError" in docs[0].page_content
    assert docs[0].metadata.get("level") == "必须"
    assert "src/components/UserAvatar.tsx" in docs[0].metadata.get("file_path", "")
    assert docs[1].metadata.get("level") == "建议"


def test_split_generic():
    long_text = "abc\n\n" * 200
    docs = split_generic(long_text, "doc-3", {"title": "g"})
    assert len(docs) > 1
    # 每个 chunk 不超过 1.5 * chunk_size
    from app.conf.settings import settings

    for d in docs:
        assert len(d.page_content) <= settings.chunk_size + settings.chunk_overlap + 50
