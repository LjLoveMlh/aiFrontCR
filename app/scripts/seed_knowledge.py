"""Bootstrap 样例灌入脚本（冷启动数据）.

执行：
    python -m app.scripts.seed_knowledge

行为：
    - 扫描 data/bootstrap/specs/ 和 data/bootstrap/reviews/
    - 自动判定 asset_type（路径 + 内容启发）
    - 通过 knowledge_repo.add_file 入库
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from app.conf.settings import settings
from app.entities.document import AssetType
from app.repositories.knowledge_repo import knowledge_repo


SPECS_DIR = settings.bootstrap_dir / "specs"
REVIEWS_DIR = settings.bootstrap_dir / "reviews"


def main() -> int:
    print("=" * 70)
    print("aiFrontCR · Bootstrap 样例灌入")
    print("=" * 70)

    # 1) 确保目录存在
    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)

    if not SPECS_DIR.exists() or not REVIEWS_DIR.exists():
        print(f"[FAIL] bootstrap 目录不存在：{SPECS_DIR} / {REVIEWS_DIR}")
        return 1

    # 2) 初始化 repo
    print("\n[1/2] 初始化知识库仓储...")
    knowledge_repo.init()
    print("   ✓ done")

    # 3) 灌入 specs
    print(f"\n[2/2] 灌入规范（{SPECS_DIR}）...")
    spec_count = 0
    for path in sorted(SPECS_DIR.glob("*.md")):
        print(f"   - {path.name}")
        try:
            doc = knowledge_repo.add_file(
                file_path=str(path),
                title=path.stem,
                asset_type=AssetType.SPEC,
                source="bootstrap",
            )
            spec_count += 1
            print(f"     ✓ {doc.chunk_count} chunks")
        except Exception as e:
            print(f"     [FAIL] {e}")
            logger.exception(f"灌入 {path} 失败")

    # 4) 灌入 reviews
    print(f"\n[2/2] 灌入评审案例（{REVIEWS_DIR}）...")
    review_count = 0
    for path in sorted(REVIEWS_DIR.glob("*.md")):
        print(f"   - {path.name}")
        try:
            doc = knowledge_repo.add_file(
                file_path=str(path),
                title=path.stem,
                asset_type=AssetType.REVIEW_CASE,
                source="bootstrap",
            )
            review_count += 1
            print(f"     ✓ {doc.chunk_count} chunks")
        except Exception as e:
            print(f"     [FAIL] {e}")
            logger.exception(f"灌入 {path} 失败")

    print("\n" + "=" * 70)
    print(f"[OK] Bootstrap 灌入完成")
    print(f"     规范：{spec_count} 个")
    print(f"     评审：{review_count} 个")
    stats = knowledge_repo.stats()
    print(f"     总文档：{stats['document_count']}")
    print(f"     总 chunks：{stats['chunk_count']}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
