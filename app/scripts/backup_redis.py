"""全量备份 Redis 向量库到 JSON.

执行：
    python -m app.scripts.backup_redis <output_path>
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.conf.settings import settings
from app.repositories.knowledge_repo import knowledge_repo


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else None
    if not out:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = str(settings.data_dir / f"backup_{ts}.json")
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"备份到：{out_path}")
    knowledge_repo.init()
    count = knowledge_repo._ensure() or 0  # type: ignore
    # 实际备份
    from app.clients.redis_client import redis_vector_client

    n = redis_vector_client.backup(str(out_path))
    print(f"[OK] 备份完成：{n} 个文档 → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
