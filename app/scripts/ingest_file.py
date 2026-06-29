"""CLI 批量导入本地文件 / 目录.

用法：
    python -m app.scripts.ingest_file <path> [--asset-type spec|review_case] [--recursive]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from app.entities.document import AssetType
from app.repositories.knowledge_repo import knowledge_repo


def main() -> int:
    parser = argparse.ArgumentParser(description="aiFrontCR · CLI 批量入库")
    parser.add_argument("path", help="文件路径或目录")
    parser.add_argument(
        "--asset-type",
        default=None,
        choices=[t.value for t in AssetType],
        help="资产类型（不指定则自动判定）",
    )
    parser.add_argument("--recursive", action="store_true", help="递归扫描目录")
    parser.add_argument("--tags", default="", help="逗号分隔的标签")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"[FAIL] 路径不存在：{path}")
        return 1

    files: list[Path] = []
    if path.is_file():
        files = [path]
    elif path.is_dir():
        glob = path.rglob if args.recursive else path.glob
        for ext in ["*.md", "*.markdown", "*.txt", "*.json"]:
            files.extend(glob(ext))

    if not files:
        print(f"[FAIL] 未发现可入库文件：{path}")
        return 1

    print(f"待入库文件：{len(files)} 个")
    knowledge_repo.init()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    asset_type = AssetType(args.asset_type) if args.asset_type else None

    ok, fail = 0, 0
    for f in files:
        try:
            doc = knowledge_repo.add_file(
                file_path=str(f),
                title=f.stem,
                asset_type=asset_type,
                tags=tags or None,
                source="ingest_cli",
            )
            print(f"  ✓ {f.name} ({doc.chunk_count} chunks)")
            ok += 1
        except Exception as e:
            print(f"  ✗ {f.name} : {e}")
            logger.exception(f"ingest {f} failed")
            fail += 1

    print(f"\n[OK] 入库完成：{ok} 成功 / {fail} 失败")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
