"""从 JSON 备份恢复 Redis 向量库.

执行：
    python -m app.scripts.restore_redis <input_path>

行为：删除现有索引，按备份重建。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from loguru import logger

from app.entities.document import AssetType, SourceType
from app.repositories.knowledge_repo import knowledge_repo


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python -m app.scripts.restore_redis <input_path>")
        return 1
    in_path = Path(sys.argv[1])
    if not in_path.exists():
        print(f"[FAIL] 文件不存在：{in_path}")
        return 1

    with open(in_path, "r", encoding="utf-8") as f:
        backup = json.load(f)

    docs = backup.get("documents", [])
    print(f"待恢复文档：{len(docs)} 个")

    knowledge_repo.init()
    ok, fail = 0, 0
    for d in docs:
        try:
            asset_type = AssetType(d.get("asset_type", "unknown"))
            source = SourceType(d.get("source", "upload"))
            # 拼接全文
            full_text = "\n\n".join(c["text"] for c in d.get("chunks", []))
            if not full_text.strip():
                continue
            knowledge_repo.add_text(
                title=d.get("title", ""),
                text=full_text,
                asset_type=asset_type,
                source=source,
                url=d.get("url") or None,
                tags=d.get("tags", []),
                level=d.get("level") or None,
                doc_id=d.get("id"),
            )
            print(f"  ✓ {d.get('title', '')}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {d.get('title', '')}: {e}")
            logger.exception(f"恢复 {d.get('id')} 失败")
            fail += 1

    print(f"\n[OK] 恢复完成：{ok} 成功 / {fail} 失败")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
